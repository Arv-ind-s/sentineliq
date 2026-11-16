"""Executable KPI gate for the trained failure model.

The gate deliberately rebuilds the held-out windows and calls the persisted
artifact.  Metrics in ``models/MANIFEST.json`` are metadata for reporting,
not evidence for this test.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from time import perf_counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.metrics import balanced_accuracy_score, precision_score, recall_score

from api.main import app
from api.schemas import LedgerResponse
from core.config import settings
from core.downtime_sim import simulate_downtime
from scripts import train


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
ARTIFACT_ROOT = ROOT / "models"
FAILURE_ARTIFACT = "failure_calibrated.joblib"


@dataclass(frozen=True)
class KpiScores:
    event_detected: int
    event_total: int
    balanced_accuracy: float
    precision: float
    recall: float
    sample_count: int
    horizon_hours: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(artifact_root: Path) -> dict[str, Any]:
    manifest_path = artifact_root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        pytest.fail(f"model manifest could not be loaded: {manifest_path}: {exc}")
    assert isinstance(manifest, dict), "model manifest must be a JSON object"
    return manifest


def _load_failure_model(artifact_root: Path, manifest: dict[str, Any]) -> Any:
    artifact_path = artifact_root / FAILURE_ARTIFACT
    expected_digest = manifest.get("artifacts", {}).get(FAILURE_ARTIFACT)
    assert isinstance(expected_digest, str), f"manifest has no checksum for {FAILURE_ARTIFACT}"
    assert artifact_path.exists(), f"failure artifact is missing: {artifact_path}"
    actual_digest = _sha256(artifact_path)
    assert actual_digest == expected_digest, (
        f"failure artifact checksum mismatch: expected {expected_digest}, got {actual_digest}"
    )
    try:
        model = joblib.load(artifact_path)
    except Exception as exc:  # joblib raises several format-specific exceptions.
        pytest.fail(f"failure artifact could not be loaded: {artifact_path}: {exc}")
    assert hasattr(model, "predict_proba"), "failure artifact does not expose predict_proba"
    return model


def _heldout_event_detection(
    dataset: train.WindowDataset,
    test_indices: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
    events: list[train.EventLabel],
    test_start_ts: int,
) -> tuple[int, int]:
    heldout_events = [event for event in events if event.machine_id in train.UNSEEN_MACHINES or event.failure_ts >= test_start_ts]
    test_machine_ids = dataset.machine_ids[test_indices]
    test_window_ends = dataset.window_ends[test_indices]
    detected = 0
    for event in heldout_events:
        event_mask = (
            (test_machine_ids == event.machine_id)
            & (test_window_ends >= event.onset_ts)
            & (test_window_ends < event.failure_ts)
        )
        if bool(np.any(probabilities[event_mask] >= threshold)):
            detected += 1
    return detected, len(heldout_events)


def _score_heldout(artifact_root: Path = ARTIFACT_ROOT) -> KpiScores:
    manifest = _load_manifest(artifact_root)
    model = _load_failure_model(artifact_root, manifest)

    events = train._load_events(DATA_ROOT)
    frame = train._load_sensor_frame(DATA_ROOT)
    dataset = train._build_windows(frame, events)
    metadata = json.loads((DATA_ROOT / "metadata.json").read_text(encoding="utf-8"))
    split = train._split_windows(dataset, metadata, events)
    features, _ = train._impute(dataset.features, split.train)
    probabilities = np.asarray(model.predict_proba(features[split.test])[:, 1], dtype=np.float64)
    labels = dataset.failure_labels[split.test]

    threshold = manifest.get("threshold_tau")
    assert isinstance(threshold, (int, float)) and 0 <= threshold <= 1, "manifest threshold_tau must be in [0, 1]"
    test_start_ts = int(metadata["base_ts_ms"] + int(metadata["days"] * 0.80) * train.DAY_MS)
    event_detected, event_total = _heldout_event_detection(dataset, split.test, probabilities, float(threshold), events, test_start_ts)
    predicted = probabilities >= float(threshold)
    return KpiScores(
        event_detected=event_detected,
        event_total=event_total,
        balanced_accuracy=float(balanced_accuracy_score(labels, predicted)),
        precision=float(precision_score(labels, predicted, zero_division=0)),
        recall=float(recall_score(labels, predicted, zero_division=0)),
        sample_count=len(labels),
        horizon_hours=int(settings.predict.horizon_hours),
    )


def test_accuracy_gate() -> None:
    """Re-score the held-out split and enforce the two KPI gates."""
    scores = _score_heldout()
    event_rate = scores.event_detected / scores.event_total if scores.event_total else 0.0
    assert event_rate >= 0.85, (
        f"per-event detection {scores.event_detected}/{scores.event_total}={event_rate:.3f} "
        f"below 0.85 (n={scores.event_total} held-out events, horizon={scores.horizon_hours}h)"
    )
    assert scores.balanced_accuracy >= 0.85, (
        f"balanced accuracy {scores.balanced_accuracy:.3f} below 0.85 "
        f"(n={scores.sample_count} held-out windows, horizon={scores.horizon_hours}h)"
    )
    assert scores.precision >= 0.80, (
        f"precision {scores.precision:.3f} below 0.80 "
        f"(n={scores.sample_count} held-out windows, horizon={scores.horizon_hours}h)"
    )
    assert scores.recall >= 0.80, (
        f"recall {scores.recall:.3f} below 0.80 "
        f"(n={scores.sample_count} held-out windows, horizon={scores.horizon_hours}h)"
    )
    print(
        f"KPI gate: per-event={scores.event_detected}/{scores.event_total} ({event_rate:.1%}), "
        f"balanced_accuracy={scores.balanced_accuracy:.3f} (n={scores.sample_count}), "
        f"precision={scores.precision:.3f}, recall={scores.recall:.3f}, horizon={scores.horizon_hours}h"
    )


def test_corrupted_failure_artifact_fails_cleanly(tmp_path: Path) -> None:
    """A damaged artifact is reported as a failed assertion, not a test error."""
    manifest = _load_manifest(ARTIFACT_ROOT)
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / FAILURE_ARTIFACT).write_bytes(b"deliberately corrupt model artifact")

    with pytest.raises(AssertionError, match="checksum mismatch"):
        _load_failure_model(tmp_path, manifest)


def _ledger_simulation(tau: float | None = None):
    connection = sqlite3.connect(DATA_ROOT / "plant.db")
    connection.row_factory = sqlite3.Row
    try:
        events = connection.execute("SELECT id AS event_id, machine_id, onset_ts, failure_ts, actual_downtime_min FROM fault_events ORDER BY id").fetchall()
        machines = connection.execute("SELECT id AS machine_id, hourly_downtime_cost_gbp FROM machines ORDER BY id").fetchall()
        history = connection.execute("SELECT machine_id, order_type, cost_gbp FROM work_orders ORDER BY workorder_id").fetchall()
        signals = connection.execute("SELECT id AS signal_id, machine_id, ts, p_failure AS probability FROM predictions WHERE source = 'historical_backfill' ORDER BY machine_id, ts").fetchall()
    finally:
        connection.close()
    assert signals, "historical time-indexed predictions must be backfilled"
    manifest = _load_manifest(ARTIFACT_ROOT)
    threshold = float(manifest.get("threshold_tau", settings.predict.threshold_tau_default)) if tau is None else tau
    return simulate_downtime(
        events,
        machines,
        signals,
        tau=threshold,
        erp_history=history,
        probability_snapshots=True,
    )


def test_downtime_reduction_accounts_for_misses_and_false_positives() -> None:
    """Check the calculation; the evaluation report owns the 20% product gate."""
    result = _ledger_simulation()
    event_count = len(result.predictive.missed_failure_ids) + result.predictive.planned_actions - result.predictive.false_positive_count
    assert event_count > 0
    recall = 1 - len(result.predictive.missed_failure_ids) / event_count
    false_positive_rate = result.predictive.false_positive_count / event_count
    implied_recall = 0.278 + 0.39 * false_positive_rate
    expected = (result.reactive.downtime_minutes - result.predictive.downtime_minutes) / result.reactive.downtime_minutes * 100
    assert result.reduction_pct == pytest.approx(expected)
    assert (result.reduction_pct >= 20.0) == (recall >= (0.20 + (90 / 320) * false_positive_rate) / (1 - 90 / 320))
    print(
        f"KPI gate #2: reduction={result.reduction_pct:.2f}%, recall={recall:.3f}, "
        f"false_positives={result.predictive.false_positive_count}, tau={result.tau:.6f}"
    )


def test_ledger_route_uses_shared_simulator_and_recomputes_under_200ms() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    default_response = client.get("/ledger")
    assert default_response.status_code == 200
    default_payload = LedgerResponse.model_validate(default_response.json())
    assert [policy.policy for policy in default_payload.policies] == ["reactive", "fixed_schedule", "predictive"]
    direct_default = _ledger_simulation()
    assert default_payload.reduction_pct == round(direct_default.reduction_pct, 2)

    started = perf_counter()
    alternate_response = client.get("/ledger?tau=0.30")
    elapsed = perf_counter() - started
    assert alternate_response.status_code == 200
    alternate_payload = LedgerResponse.model_validate(alternate_response.json())
    assert elapsed < 0.20
    assert alternate_payload.tau == 0.30
    assert alternate_payload.model_dump() != default_payload.model_dump()
    assert alternate_payload.reduction_pct == round(_ledger_simulation(0.30).reduction_pct, 2)
