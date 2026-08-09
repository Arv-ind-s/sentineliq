"""Deterministic sensor-pipeline agent.

The Sensor Agent owns one machine window from storage read through prediction.
It deliberately has no LLM dependency: every value it emits is derived from
the window, the versioned feature extractor, and an injected predictor.  The
storage and predictor collaborators are protocols rather than concrete
implementations so the serving seams can be replaced without changing this
agent.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agents.base import AgentResult, RunContext
from core.config import settings
from core.db import create_schema, session_scope
from core.features import FEATURE_VERSION, extract
from core.ingest import read_ingest_points, read_parquet_points
from core.models import Alert, Evidence, Prediction
from core.preprocess import CHANNELS, QualityReport, preprocess_window


logger = logging.getLogger(__name__)


class TSDBClient(Protocol):
    """Windowed sensor storage seam used by the agent."""

    def read_window(
        self,
        machine_id: str,
        t0: Any,
        t1: Any,
        channels: Sequence[str],
    ) -> pd.DataFrame: ...


def _epoch_ms(value: Any) -> int:
    """Normalise API and replay timestamps for windowed storage reads."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return int(round(number if abs(number) >= 1e11 else number * 1_000))
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(UTC)
    return int(timestamp.tz_convert(UTC).timestamp() * 1_000)


class PlantTSDBClient:
    """Production sensor-window adapter over Parquet plus live ingest rows.

    Parquet is the immutable historical source.  Rows accepted through the
    ingest boundary are overlaid by timestamp so a diagnosis can immediately
    consume newer PLC/rehearsal data without duplicating preprocessing logic.
    """

    def __init__(self, *, database_url: str | None = None, sensor_root: str | Path | None = None) -> None:
        self.database_url = database_url or settings.db_url
        self.sensor_root = Path(sensor_root) if sensor_root is not None else None

    def read_window(self, machine_id: str, t0: Any, t1: Any, channels: Sequence[str]) -> pd.DataFrame:
        start_ms = _epoch_ms(t0)
        end_ms = _epoch_ms(t1)
        if end_ms < start_ms:
            raise ValueError("sensor window end must not precede its start")
        selected = tuple(dict.fromkeys(str(channel) for channel in channels))
        historical = read_parquet_points(machine_id, start_ms, end_ms, self.sensor_root)
        live = read_ingest_points(machine_id, start_ms, end_ms, self.database_url)
        merged = {point.ts_ms: point for point in historical}
        merged.update({point.ts_ms: point for point in live})
        rows = [
            {
                "machine_id": machine_id,
                "ts_utc_ms": point.ts_ms,
                **{channel: point.channels.get(channel) for channel in selected},
            }
            for point in sorted(merged.values(), key=lambda item: item.ts_ms)
        ]
        columns = ["machine_id", "ts_utc_ms", *selected]
        return pd.DataFrame(rows, columns=columns)


class Predictor(Protocol):
    """Model-serving seam used by the agent."""

    def score(self, features: np.ndarray, machine_meta: Mapping[str, Any]) -> Any: ...


PreprocessFn = Callable[[pd.DataFrame, Any], tuple[pd.DataFrame, QualityReport]]
FeatureFn = Callable[[pd.DataFrame], np.ndarray]
AnomalyFn = Callable[[pd.DataFrame], list[dict[str, Any]]]


@dataclass(frozen=True)
class SensorEvidence:
    """One concrete sensor citation bound to a physical window statistic."""

    id: str
    kind: str
    channel: str
    t_start: int
    t_end: int
    stat: str
    value: float
    quality_flags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["quality_flags"] = list(self.quality_flags)
        return payload


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def _epoch_ms(value: Any) -> int:
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        number = float(value)
        return int(round(number if abs(number) >= 1e11 else number * 1_000))
    if isinstance(value, datetime):
        timestamp = pd.Timestamp(value)
    else:
        timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return int(timestamp.tz_convert("UTC").timestamp() * 1_000)


def _window_bounds(window: pd.DataFrame, t0: Any, t1: Any) -> tuple[int, int]:
    if "ts_utc_ms" in window.columns and not window.empty:
        values = pd.to_numeric(window["ts_utc_ms"], errors="coerce").dropna()
        if not values.empty:
            return int(values.min()), int(values.max())
    return _epoch_ms(t0), _epoch_ms(t1)


def _quality_dict(report: QualityReport) -> dict[str, Any]:
    return asdict(report)


def detect_anomaly_windows(window: pd.DataFrame) -> list[dict[str, Any]]:
    """Detect deterministic, contiguous three-sigma channel excursions.

    The detector intentionally reports windows rather than individual points.
    A constant channel or a channel with fewer than three valid observations
    produces no anomaly; it is handled by preprocessing quality flags instead.
    """

    if "ts_utc_ms" not in window.columns:
        return []
    anomalies: list[dict[str, Any]] = []
    for channel in (
        "vibration_rms_mm_s",
        "vibration_kurtosis",
        "temp_bearing_c",
        "temp_motor_c",
        "pressure_bar",
        "current_a",
        "rpm",
        "ambient_temp_c",
    ):
        if channel not in window.columns:
            continue
        values = pd.to_numeric(window[channel], errors="coerce")
        valid = values.dropna()
        if len(valid) < 3:
            continue
        mean = float(valid.mean())
        std = float(valid.std(ddof=0))
        if std <= 1e-12:
            continue
        mask = (values - mean).abs() > 3.0 * std
        indices = np.flatnonzero(mask.to_numpy(dtype=bool))
        if not len(indices):
            continue
        for group in np.split(indices, np.where(np.diff(indices) > 1)[0] + 1):
            if not len(group):
                continue
            segment = values.iloc[group]
            timestamps = pd.to_numeric(window["ts_utc_ms"].iloc[group], errors="coerce").dropna()
            if timestamps.empty:
                continue
            anomalies.append(
                {
                    "channel": channel,
                    "t_start": int(timestamps.min()),
                    "t_end": int(timestamps.max()),
                    "stat": "max_deviation",
                    "value": _finite_float(segment.abs().max()),
                }
            )
    return anomalies


def _sensor_evidence(window: pd.DataFrame, quality: QualityReport) -> list[SensorEvidence]:
    if "ts_utc_ms" not in window.columns:
        return []
    t_start, t_end = _window_bounds(window, 0, 0)
    result: list[SensorEvidence] = []
    channel_order = (
        "vibration_rms_mm_s",
        "temp_bearing_c",
        "temp_motor_c",
        "pressure_bar",
        "current_a",
        "rpm",
        "vibration_kurtosis",
        "ambient_temp_c",
    )
    flags = tuple(quality.suspect_channels)
    for channel in channel_order:
        if channel not in window.columns:
            continue
        values = pd.to_numeric(window[channel], errors="coerce").dropna()
        if values.empty:
            continue
        result.append(
            SensorEvidence(
                id=f"S{len(result) + 1}",
                kind="sensor",
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                stat="mean",
                value=_finite_float(values.mean()),
                quality_flags=flags,
            )
        )
        if len(result) == 5:
            break
    return result


def _prediction_values(score: Any, quality: QualityReport) -> dict[str, Any]:
    p_failure = min(max(_finite_float(_field(score, "p_failure_24h"), 0.0), 0.0), 1.0)
    mode_probs = _field(score, "mode_probs", {}) or {}
    if not isinstance(mode_probs, Mapping):
        mode_probs = {}
    mode_probs = {str(key): min(max(_finite_float(value), 0.0), 1.0) for key, value in mode_probs.items()}
    return {
        "p_failure_24h": p_failure,
        "rul_p10": max(0.0, _finite_float(_field(score, "rul_p10"))),
        "rul_p50": max(0.0, _finite_float(_field(score, "rul_p50"))),
        "rul_p90": max(0.0, _finite_float(_field(score, "rul_p90"))),
        "mode_probs": mode_probs,
        "top_features": _json_safe(_field(score, "top_features", [])),
        "quality": _quality_dict(quality),
        "predictor": str(_field(score, "predictor", "unknown")),
        "model_version": str(_field(score, "model_version", FEATURE_VERSION)),
        "confidence": min(max(_finite_float(_field(score, "confidence"), quality.completeness), 0.0), 1.0),
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value if not isinstance(value, float) or np.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(child) for child in value.tolist()]
    if isinstance(value, (np.integer, np.floating)):
        return _json_safe(value.item())
    return str(value)


def _persist_prediction_and_evidence(
    *,
    database_url: str,
    prediction_id: str,
    machine_id: str,
    ts: int,
    prediction: Mapping[str, Any],
    evidence: Sequence[SensorEvidence],
) -> None:
    """Persist the active prediction and its citations in one transaction."""

    create_schema(database_url)
    risk = min(max(float(prediction["p_failure_24h"]) * 100.0, 0.0), 100.0)
    band = "CRITICAL" if risk >= 75 else "HIGH" if risk >= 55 else "MEDIUM" if risk >= 35 else "LOW"
    with session_scope(database_url) as session:
        values = {
            "id": prediction_id,
            "machine_id": machine_id,
            "ts": ts,
            "p_failure": prediction["p_failure_24h"],
            "rul_p10": prediction["rul_p10"],
            "rul_p50": prediction["rul_p50"],
            "rul_p90": prediction["rul_p90"],
            "mode_probs": dict(prediction["mode_probs"]),
            "risk": risk,
            "band": band,
            "confidence": prediction["confidence"],
            "predictor": prediction["predictor"],
            "model_version": prediction["model_version"],
            "source": "serving",
        }
        statement = sqlite_insert(Prediction).values(values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["machine_id", "ts", "model_version"],
                set_={key: statement.excluded[key] for key in values if key != "id"},
            )
        )
        for item in evidence:
            payload = item.as_dict()
            evidence_statement = sqlite_insert(Evidence).values(
                prediction_id=prediction_id,
                citation_id=item.id,
                kind=item.kind,
                payload=payload,
            )
            session.execute(
                evidence_statement.on_conflict_do_update(
                    index_elements=["prediction_id", "citation_id"],
                    set_={"kind": evidence_statement.excluded.kind, "payload": evidence_statement.excluded.payload},
                )
            )


def _persist_integrity_alert(*, database_url: str, machine_id: str, ts: int, reason: str) -> str:
    create_schema(database_url)
    alert_id = f"sensor-integrity-{machine_id.lower()}-{ts}"
    with session_scope(database_url) as session:
        row = session.get(Alert, alert_id)
        if row is None:
            session.add(
                Alert(
                    id=alert_id,
                    machine_id=machine_id,
                    kind="data_integrity",
                    severity="critical",
                    state="open",
                    created_ts=ts,
                    updated_ts=ts,
                )
            )
        else:
            row.updated_ts = ts
    logger.warning("sensor_data_integrity_alert", extra={"machine_id": machine_id, "reason": reason, "alert_id": alert_id})
    return alert_id


class SensorAgent:
    """Run the complete deterministic sensor path for one machine window."""

    name = "sensor"
    version = "v1"
    model_name = "deterministic-pipeline"
    provider = "local"

    def __init__(
        self,
        tsdb: TSDBClient,
        predictor: Predictor,
        *,
        database_url: str | None = None,
        preprocess_fn: PreprocessFn = preprocess_window,
        feature_fn: FeatureFn = extract,
        anomaly_fn: AnomalyFn = detect_anomaly_windows,
        read_retry_delays_s: Sequence[float] = (0.5, 1.0, 2.0),
    ) -> None:
        if len(read_retry_delays_s) != 3 or any(delay < 0 for delay in read_retry_delays_s):
            raise ValueError("read_retry_delays_s must contain three non-negative delays")
        self.tsdb = tsdb
        self.predictor = predictor
        self.database_url = database_url or settings.db_url
        self.preprocess_fn = preprocess_fn
        self.feature_fn = feature_fn
        self.anomaly_fn = anomaly_fn
        self.read_retry_delays_s = tuple(float(delay) for delay in read_retry_delays_s)

    def _read(self, machine_id: str, t0: Any, t1: Any, channels: Sequence[str]) -> tuple[pd.DataFrame | None, int, list[str]]:
        errors: list[str] = []
        for attempt, delay in enumerate(self.read_retry_delays_s):
            try:
                window = self.tsdb.read_window(machine_id, t0, t1, channels)
                if not isinstance(window, pd.DataFrame):
                    raise TypeError("read_window must return a pandas DataFrame")
                return window, attempt, errors
            except Exception as exc:  # storage failures are isolated from the orchestrator
                errors.append(f"{type(exc).__name__}: {exc}".strip())
                if attempt < len(self.read_retry_delays_s) - 1:
                    time.sleep(delay)
        return None, len(self.read_retry_delays_s) - 1, errors

    def run(self, ctx: RunContext) -> AgentResult:
        started = time.perf_counter()
        payload = ctx.payload
        machine_id = str(payload.get("machine_id", "")).strip()
        t0 = payload.get("t0", payload.get("window_start"))
        t1 = payload.get("t1", payload.get("window_end"))
        if not machine_id or t0 is None or t1 is None:
            return AgentResult(ok=False, output={"reason": "invalid_sensor_window"}, errors=["machine_id, t0, and t1 are required"])

        channels = tuple(payload.get("channels", CHANNELS))
        window, retries, read_errors = self._read(machine_id, t0, t1, channels)
        if window is None:
            return AgentResult(
                ok=False,
                output={"machine_id": machine_id, "reason": "sensor_unavailable"},
                errors=read_errors,
                retries=retries,
                fallback_used=True,
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            cleaned, quality = self.preprocess_fn(window, payload.get("preprocess_config"))
            quality_payload = _quality_dict(quality)
            bounds = _window_bounds(cleaned, t0, t1)
            if quality.quality == "INSUFFICIENT":
                alert_id = _persist_integrity_alert(
                    database_url=self.database_url,
                    machine_id=machine_id,
                    ts=bounds[1],
                    reason="INSUFFICIENT sensor quality",
                )
                return AgentResult(
                    ok=True,
                    output={
                        "machine_id": machine_id,
                        "refused": True,
                        "reason": "insufficient_sensor_quality",
                        "quality": quality_payload,
                        "alerts": [{"id": alert_id, "kind": "data_integrity", "route": "maintenance-systems", "severity": "critical"}],
                        "evidence": [],
                    },
                    evidence=[],
                    confidence=quality.completeness,
                    retries=retries,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )

            features = np.asarray(self.feature_fn(cleaned), dtype=np.float32)
            if features.shape != (41,) or not np.isfinite(features).all():
                raise ValueError(f"feature vector must be finite and shape (41,), got {features.shape}")
            score = self.predictor.score(features, payload.get("machine_meta", {}))
            prediction = _prediction_values(score, quality)
            evidence = _sensor_evidence(cleaned, quality)
            if not 3 <= len(evidence) <= 5:
                raise ValueError(f"sensor evidence must contain 3-5 concrete items, got {len(evidence)}")
            model_version = str(prediction["model_version"])
            version_digest = hashlib.sha256(model_version.encode("utf-8")).hexdigest()[:8]
            prediction_id = f"pred-{machine_id.lower()}-{bounds[1]}-{version_digest}"
            _persist_prediction_and_evidence(
                database_url=self.database_url,
                prediction_id=prediction_id,
                machine_id=machine_id,
                ts=bounds[1],
                prediction=prediction,
                evidence=evidence,
            )
            output = {
                "prediction_id": prediction_id,
                "machine_id": machine_id,
                **prediction,
                "anomaly_windows": _json_safe(self.anomaly_fn(cleaned)),
                "evidence": [item.as_dict() for item in evidence],
                "refused": False,
            }
            return AgentResult(
                ok=True,
                output=output,
                evidence=[item.as_dict() for item in evidence],
                confidence=prediction["confidence"],
                retries=retries,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            logger.exception("sensor_pipeline_failed", extra={"machine_id": machine_id, "trace_id": ctx.trace_id})
            return AgentResult(
                ok=False,
                output={"machine_id": machine_id, "reason": "sensor_processing_failed"},
                errors=[f"{type(exc).__name__}: {exc}"],
                retries=retries,
                latency_ms=(time.perf_counter() - started) * 1000,
            )


__all__ = [
    "PlantTSDBClient",
    "Predictor",
    "SensorAgent",
    "SensorEvidence",
    "TSDBClient",
    "detect_anomaly_windows",
]
