"""Persist honest time-indexed historical predictions for the downtime ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import ROOT, settings
from core.db import create_schema, session_scope
from core.models import Approval, Machine, Prediction
from core.predict import load_predictor
from scripts import train


def _prediction_id(machine_id: str, window_end: int, model_version: str) -> str:
    digest = hashlib.sha256(model_version.encode("utf-8")).hexdigest()[:8]
    return f"pred-{machine_id.lower()}-{window_end}-{digest}"


def backfill(database_url: str, data_root: Path = ROOT / "data") -> dict[str, object]:
    """Score telemetry windows only; fault-event labels never enter this path."""

    create_schema(database_url)
    frame = train._load_sensor_frame(data_root)
    windows = train._build_windows(frame, [])
    predictor = load_predictor()
    batch_predictor = getattr(predictor, "primary", predictor)
    with session_scope(database_url) as session:
        machine_meta = {
            row.id: {"criticality": row.criticality, "hourly_downtime_cost_gbp": row.hourly_downtime_cost_gbp}
            for row in session.scalars(select(Machine))
        }

    if all(hasattr(batch_predictor, name) for name in ("failure_model", "fault_model", "rul_models", "feature_medians")):
        feature_values = np.asarray(windows.features, dtype=np.float32)
        medians = np.asarray(batch_predictor.feature_medians, dtype=np.float32).reshape(1, -1)
        feature_values = np.where(np.isfinite(feature_values), feature_values, medians)
        failure_probabilities = np.asarray(batch_predictor.failure_model.predict_proba(feature_values)[:, 1], dtype=float)
        fault_values = np.asarray(batch_predictor.fault_model.predict_proba(feature_values), dtype=float)
        fault_classes = [str(value) for value in batch_predictor.fault_model.classes_]
        rul_values = np.vstack([model.predict(feature_values) for model in batch_predictor.rul_models]).T
        scored = [
            (
                float(failure_probabilities[index]),
                {label: float(value) for label, value in zip(fault_classes, fault_values[index])},
                sorted(float(value) for value in rul_values[index]),
                float(np.clip(0.5 + abs(failure_probabilities[index] - 0.5), 0.0, 1.0)),
                str(batch_predictor.predictor_name),
                str(batch_predictor.model_version),
            )
            for index in range(len(feature_values))
        ]
    else:
        scored = []
        for machine_id, features in zip(windows.machine_ids, windows.features):
            result = predictor.score(features, machine_meta.get(str(machine_id), {}))
            scored.append(
                (
                    float(result.p_failure_24h), dict(result.mode_probs),
                    [float(result.rul_p10), float(result.rul_p50), float(result.rul_p90)],
                    float(result.confidence), str(result.predictor), str(result.model_version),
                )
            )

    rows: list[dict[str, object]] = []
    model_versions: set[str] = set()
    for machine_id, window_end, score in zip(windows.machine_ids, windows.window_ends, scored):
        machine = str(machine_id)
        p_failure, mode_probs, rul, confidence, predictor_name, model_version = score
        model_versions.add(model_version)
        risk = min(max(p_failure * 100.0, 0.0), 100.0)
        rows.append(
            {
                "id": _prediction_id(machine, int(window_end), model_version),
                "machine_id": machine,
                "ts": int(window_end),
                "p_failure": p_failure,
                "rul_p10": rul[0],
                "rul_p50": rul[1],
                "rul_p90": rul[2],
                "mode_probs": mode_probs,
                "risk": risk,
                "band": "CRITICAL" if risk >= 75 else "HIGH" if risk >= 55 else "MEDIUM" if risk >= 35 else "LOW",
                "confidence": confidence,
                "predictor": predictor_name,
                "model_version": model_version,
                "source": "historical_backfill",
            }
        )

    with session_scope(database_url) as session:
        for start in range(0, len(rows), 250):
            statement = sqlite_insert(Prediction).values(rows[start : start + 250])
            updates = {
                column: getattr(statement.excluded, column)
                for column in (
                    "id", "p_failure", "rul_p10", "rul_p50", "rul_p90", "mode_probs",
                    "risk", "band", "confidence", "predictor", "source",
                )
            }
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["machine_id", "ts", "model_version"],
                    set_=updates,
                )
            )
    return {"predictions": len(rows), "model_versions": sorted(model_versions), "used_fault_events": False}


def seed_demo_approval(database_url: str) -> str:
    """Create one real, reviewable PUMP-07 A0 approval for a fresh demo."""

    with session_scope(database_url) as session:
        prediction = session.scalars(
            select(Prediction)
            .where(Prediction.machine_id == "PUMP-07", Prediction.source == "historical_backfill")
            .order_by(Prediction.p_failure.desc(), Prediction.ts.desc())
        ).first()
        if prediction is None:
            raise RuntimeError("PUMP-07 historical prediction is unavailable; run the ledger backfill first")
        request_key = f"{prediction.id}:A0:planner"
        existing = session.scalars(select(Approval).where(Approval.request_key == request_key)).first()
        if existing is not None:
            return f"approval-{existing.id}"
        payload = {
            "prediction_id": prediction.id,
            "asset_id": prediction.machine_id,
            "fault_code": "BRG-01",
            "description": "Inspect the PUMP-07 drive-end bearing using the reviewed predictive-maintenance evidence",
            "priority": "high",
            "scheduled_start": None,
            "technician_notes": "Review vibration and bearing-temperature evidence before the planned maintenance window.",
            "parts": ["Drive-end bearing kit"],
            "labour_hours": 2.5,
            "downtime_minutes": 90,
            "cost_gbp": 1800.0,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        approval = Approval(
            prediction_id=prediction.id,
            autonomy_level="A0",
            required_role="planner",
            state="pending",
            request_key=request_key,
            action_payload=payload,
            payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            erp_sync_status="not_requested",
        )
        session.add(approval)
        session.flush()
        return f"approval-{approval.id}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.db_url)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    report = backfill(args.database_url, args.data_root)
    report["demo_approval"] = seed_demo_approval(args.database_url)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
