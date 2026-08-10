"""Model-dependent verification for the four planted demonstration scenarios.

This module deliberately reads the generated Parquet partitions and ERP
history instead of using scenario fixtures.  It is invoked by
``scripts/seed.py`` after every training run.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pytest

from core.features import FEATURE_NAMES
from core.fusion import HistoryEvidence, MachineContext, SensorEvidence, fuse
from core.predict import PredictionResult, load_verified_predictor
from core.preprocess import CHANNELS, QualityReport, preprocess_window
from core.config import settings
from scripts import train


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("SENTINEL_DATA_ROOT", ROOT / "data")).resolve()
MODEL_ROOT = Path(os.environ.get("SENTINEL_MODEL_ROOT", ROOT / settings.predict.artifacts_dir)).resolve()
ERP_ROOT = Path(os.environ.get("SENTINEL_ERP_ROOT", ROOT / "erp" / "erp.db")).resolve()
WINDOW_MS = int(settings.features.window_hours * 60 * 60 * 1000)
STEP_MS = 30 * 60 * 1000
SPEEDUP = float(settings.stream.speedup)
_PUMP_PROFILE: tuple[tuple[int, float], ...] | None = None


@dataclass(frozen=True)
class ScoredWindow:
    score: PredictionResult | None
    quality: QualityReport
    decision: Any


class ParquetSource:
    """Read real generated telemetry through the Sensor Agent storage seam."""

    def __init__(self, root: Path) -> None:
        self.dataset = ds.dataset(root / "sensors", format="parquet", partitioning="hive")
        self.metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        self._frames: dict[str, pd.DataFrame] = {}

    def read_window(self, machine_id: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        columns = ["canonical_ts_ms", "ts_utc_ms", "clock_offset_s", "machine_id", *CHANNELS]
        frame = self._frames.get(machine_id)
        if frame is None:
            table = self.dataset.to_table(columns=columns, filter=ds.field("machine_id") == machine_id)
            frame = table.to_pandas().sort_values("canonical_ts_ms", kind="mergesort").reset_index(drop=True)
            self._frames[machine_id] = frame
        frame = frame[(frame["canonical_ts_ms"] > int(start_ms)) & (frame["canonical_ts_ms"] <= int(end_ms))].copy()
        unit_map = {machine_id: {"temp_bearing_c": "F"}} if machine_id in self.metadata.get("defects", {}).get("fahrenheit_machines", []) else {}
        frame.attrs["unit_maps"] = unit_map
        return frame


@pytest.fixture(scope="module")
def context() -> tuple[ParquetSource, Any, dict[str, Any], int]:
    manifest = json.loads((MODEL_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    tau = manifest.get("threshold_tau")
    assert isinstance(tau, (int, float)), "model manifest threshold_tau is missing"
    source = ParquetSource(DATA_ROOT)
    return source, load_verified_predictor(MODEL_ROOT), manifest, int(json.loads((DATA_ROOT / "metadata.json").read_text(encoding="utf-8"))["t0_ts_ms"])


def _feature_vector(cleaned: pd.DataFrame) -> np.ndarray:
    """Use the exact feature adapter used by the persisted A-04 artifacts."""

    values = cleaned.loc[:, list(CHANNELS)].to_numpy(dtype=np.float32)
    vector = train._window_features(values)
    assert vector.shape == (len(FEATURE_NAMES),)
    assert np.isfinite(vector).all()
    return vector


def _score(
    source: ParquetSource,
    predictor: Any,
    machine_id: str,
    start_ms: int,
    end_ms: int,
    history: HistoryEvidence,
    *,
    sensor_drift_signal: bool | None = None,
) -> ScoredWindow:
    raw = source.read_window(machine_id, start_ms, end_ms)
    cleaned, quality = preprocess_window(raw)
    if quality.quality == "INSUFFICIENT":
        decision = fuse(
            SensorEvidence(0.0, 0.0, "unknown", quality.completeness, 0, sensor_drift_signal=sensor_drift_signal),
            history,
            MachineContext(criticality="major"),
        )
        return ScoredWindow(None, quality, decision)
    score = predictor.score(_feature_vector(cleaned), {"machine_id": machine_id})
    mode = max(score.mode_probs, key=score.mode_probs.get, default="unknown")
    decision = fuse(
        SensorEvidence(
            p_failure_24h=score.p_failure_24h,
            rul_p50_days=score.rul_p50,
            predicted_mode=mode,
            data_quality=quality.completeness,
            evidence_count=5,
            sensor_drift_signal=sensor_drift_signal,
        ),
        history,
        MachineContext(criticality="major"),
    )
    return ScoredWindow(score, quality, decision)


def _pump_profile(source: ParquetSource, predictor: Any, t0_ms: int) -> tuple[tuple[int, float], ...]:
    global _PUMP_PROFILE
    if _PUMP_PROFILE is not None:
        return _PUMP_PROFILE
    history = HistoryEvidence(dominant_mode="bearing_wear", history_prior=0.62)
    lower_window_s = float(settings.stream.demo_alert_window_s[0])
    upper_window_s = float(settings.stream.demo_alert_window_s[1])
    first_end = t0_ms + int(max(0.0, lower_window_s - 1.0) * SPEEDUP * 1000.0)
    final_end = t0_ms + int((upper_window_s + 1.0) * SPEEDUP * 1000.0)
    profile: list[tuple[int, float]] = []
    for end_ms in range(first_end, final_end + STEP_MS, STEP_MS):
        result = _score(source, predictor, "PUMP-07", end_ms - WINDOW_MS, end_ms, history)
        if result.score is None:
            continue
        profile.append((end_ms, result.decision.risk / 100.0))
    _PUMP_PROFILE = tuple(profile)
    return _PUMP_PROFILE


def _pump_crossing(source: ParquetSource, predictor: Any, t0_ms: int, tau: float) -> float | None:
    profile = _pump_profile(source, predictor, t0_ms)
    previous_end: int | None = None
    previous_risk: float | None = None
    for end_ms, risk in profile:
        if risk >= tau:
            if previous_end is not None and previous_risk is not None and risk > previous_risk >= 0.0:
                fraction = (tau - previous_risk) / (risk - previous_risk)
                crossing_end = previous_end + fraction * (end_ms - previous_end)
                return (crossing_end - t0_ms) / (SPEEDUP * 1000.0)
            return (end_ms - t0_ms) / (SPEEDUP * 1000.0)
        previous_end = end_ms
        previous_risk = risk
    return None


def _append_measured_crossing(crossing_s: float, tau: float) -> None:
    notes_path = DATA_ROOT / "DEMO_NOTES.md"
    notes = notes_path.read_text(encoding="utf-8")
    marker = "## Model-integrity measurement"
    if marker in notes:
        notes = notes.split(marker, 1)[0].rstrip() + "\n"
    notes += f"\n{marker}\n\n- PUMP-07 fused risk crossed tau={tau:.6f} at {crossing_s:.1f}s of the 600x replay.\n"
    notes_path.write_text(notes, encoding="utf-8")


def test_pump_07_crosses_tuned_tau_in_demo_window(context: tuple[ParquetSource, Any, dict[str, Any], int]) -> None:
    source, predictor, manifest, t0_ms = context
    tau = float(manifest["threshold_tau"])
    crossing = _pump_crossing(source, predictor, t0_ms, tau)
    lower, upper = (float(value) for value in settings.stream.demo_alert_window_s)
    assert crossing is not None, f"PUMP-07 did not cross tuned tau={tau:.6f}"
    assert lower <= crossing <= upper, f"PUMP-07 crossing observed at {crossing:.1f}s; expected {lower:.0f}–{upper:.0f}s"
    _append_measured_crossing(crossing, tau)


def test_pump_crossing_is_coupled_to_tau(context: tuple[ParquetSource, Any, dict[str, Any], int]) -> None:
    source, predictor, manifest, t0_ms = context
    tau = float(manifest["threshold_tau"])
    base = _pump_crossing(source, predictor, t0_ms, tau)
    lower = _pump_crossing(source, predictor, t0_ms, tau - 0.05)
    upper = _pump_crossing(source, predictor, t0_ms, tau + 0.05)
    assert base is not None and lower is not None and upper is not None, "PUMP-07 threshold sensitivity could not be measured"
    assert lower < base < upper, f"PUMP-07 crossing is not monotonic with tau: lower={lower:.1f}s, base={base:.1f}s, upper={upper:.1f}s"


def test_press_03_is_instrumentation_drift_not_mechanical(context: tuple[ParquetSource, Any, dict[str, Any], int]) -> None:
    source, predictor, _, t0_ms = context
    drift_start = t0_ms + 2 * 24 * 60 * 60 * 1000
    baseline = source.read_window("PRESS-03", drift_start - 6 * 60 * 60 * 1000, drift_start)
    during = source.read_window("PRESS-03", drift_start, drift_start + 6 * 60 * 60 * 1000)
    pressure_shift = float(during["pressure_bar"].median() - baseline["pressure_bar"].median())
    bearing_shift = float(during["temp_bearing_c"].median() - baseline["temp_bearing_c"].median())
    drift_signal = pressure_shift >= 0.5 or bearing_shift >= 1.0
    result = _score(
        source,
        predictor,
        "PRESS-03",
        drift_start,
        drift_start + 6 * 60 * 60 * 1000,
        HistoryEvidence(transducer_replacements_90d=2),
        sensor_drift_signal=drift_signal,
    )
    assert drift_signal, f"PRESS-03 drift signal was not observed: pressure_shift={pressure_shift:.3f}, bearing_shift={bearing_shift:.3f}"
    assert "sensor_drift_suspected" in result.decision.hard_rules, f"PRESS-03 hard rules were {result.decision.hard_rules}"
    assert result.decision.action_class == "verify_instrumentation", f"PRESS-03 action was {result.decision.action_class!r}, not instrumentation verification"


def test_cnc_11_preserves_conflicting_history(context: tuple[ParquetSource, Any, dict[str, Any], int]) -> None:
    source, predictor, _, t0_ms = context
    end_ms = t0_ms + 30 * 60 * 60 * 1000
    result = _score(source, predictor, "CNC-11", end_ms - WINDOW_MS, end_ms, HistoryEvidence(dominant_mode="PMP-02"))
    assert result.score is not None, "CNC-11 did not produce a prediction"
    agreeing = _score(source, predictor, "CNC-11", end_ms - WINDOW_MS, end_ms, HistoryEvidence(dominant_mode=max(result.score.mode_probs, key=result.score.mode_probs.get)))
    assert result.decision.agreement == "conflict", f"CNC-11 agreement was {result.decision.agreement!r}"
    assert "evidence_conflict" in result.decision.hard_rules, f"CNC-11 hard rules were {result.decision.hard_rules}"
    assert result.decision.confidence == pytest.approx(agreeing.decision.confidence * settings.fusion.rules.evidence_conflict_factor, abs=0.01)


def test_conv_14_withholds_prediction_during_connectivity_gap(context: tuple[ParquetSource, Any, dict[str, Any], int]) -> None:
    source, predictor, _, t0_ms = context
    end_ms = t0_ms + 34 * 60 * 60 * 1000
    result = _score(source, predictor, "CONV-14", end_ms - WINDOW_MS, end_ms, HistoryEvidence())
    assert result.quality.quality == "INSUFFICIENT", f"CONV-14 quality was {result.quality.quality!r}"
    assert result.decision.prediction_status == "NO_PREDICTION", f"CONV-14 status was {result.decision.prediction_status!r}"
    assert result.decision.action_class == "NO_PREDICTION", f"CONV-14 action was {result.decision.action_class!r}"
    assert result.decision.alerts and result.decision.alerts[0].kind == "data", f"CONV-14 alerts were {result.decision.alerts!r}"
