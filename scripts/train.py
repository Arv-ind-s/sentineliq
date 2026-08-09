"""Train the SentinelPdM MVP models and write a reproducible artifact manifest.

This task-local pipeline intentionally contains a small feature/window adapter
because the separately assigned B-05 and B-15 modules are not present yet.
The adapter is private to training: it is not a serving contract and must be
replaced by ``core.features``/``core.tsdb`` when those tasks land.

The script never writes ``config.yaml``.  All generated files are model
artifacts under ``models/`` and are referenced by ``MANIFEST.json`` with a
sha256 digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from sklearn.ensemble import (
    GradientBoostingRegressor,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.inspection import permutation_importance
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sqlalchemy import select

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    # Give joblib a stable import path when this file is launched as the
    # documented ``python scripts/train.py`` command.
    sys.modules.setdefault("scripts.train", sys.modules[__name__])

from core.config import ROOT, settings
from core.models import FaultEvent
from core.db import session_scope
from data.generate import CHANNELS, FAILURE_MODES


FEATURE_VERSION = settings.features.version
SEED = settings.data.seed
DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
WINDOW_MS = settings.features.window_hours * HOUR_MS
STRIDE_MS = settings.features.stride_hours * HOUR_MS
HORIZON_MS = settings.predict.horizon_hours * HOUR_MS
UNSEEN_MACHINES = ("PUMP-07", "CNC-11", "CONV-14")
DRIFT_PRESSURE_SHIFT = 0.5
DRIFT_BEARING_SHIFT = 1.0


class TrainingDataError(RuntimeError):
    """Raised when the generated data cannot satisfy the training contract."""


@dataclass(frozen=True)
class EventLabel:
    event_id: int
    machine_id: str
    mode: str
    onset_ts: int
    failure_ts: int


@dataclass(frozen=True)
class WindowDataset:
    features: np.ndarray
    machine_ids: np.ndarray
    window_ends: np.ndarray
    failure_labels: np.ndarray
    mode_labels: np.ndarray
    rul_days: np.ndarray
    target_event_ids: np.ndarray


@dataclass(frozen=True)
class Split:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray


class OrderedQuantileRegressor:
    """Persisted RUL wrapper that enforces a valid interval at inference."""

    def __init__(self, models: tuple[GradientBoostingRegressor, ...], position: int) -> None:
        self.models = models
        self.position = position

    def predict(self, features: np.ndarray) -> np.ndarray:
        raw = np.vstack([model.predict(features) for model in self.models])
        ordered = np.maximum.accumulate(raw, axis=0)
        return ordered[self.position]


OrderedQuantileRegressor.__module__ = "scripts.train"


def _feature_names() -> tuple[str, ...]:
    names: list[str] = []
    for channel in CHANNELS:
        names.extend((f"{channel}_mean", f"{channel}_std", f"{channel}_range", f"{channel}_slope"))
    names.extend(
        (
            "vibration_rms_energy",
            "vibration_kurtosis_moment",
            "vibration_crest_factor",
            "vibration_peak_to_peak",
            "vibration_band_energy_ratio",
            "temp_bearing_minus_ambient",
            "current_to_rpm_ratio",
            "vibration_load_residual",
            "window_completeness",
        )
    )
    if len(names) != 41:
        raise AssertionError(f"temporary training feature contract must contain 41 values, got {len(names)}")
    return tuple(names)


FEATURE_NAMES = _feature_names()


def _finite_values(values: np.ndarray) -> np.ndarray:
    return values[np.isfinite(values)]


def _median(values: np.ndarray, default: float = 0.0) -> float:
    valid = _finite_values(values)
    return float(np.median(valid)) if valid.size else default


def _slope(values: np.ndarray) -> float:
    valid_mask = np.isfinite(values)
    if int(valid_mask.sum()) < 2:
        return 0.0
    x = np.flatnonzero(valid_mask).astype(np.float64)
    y = values[valid_mask].astype(np.float64)
    centered_x = x - x.mean()
    denominator = float(np.dot(centered_x, centered_x))
    return float(np.dot(centered_x, y - y.mean()) / denominator) if denominator else 0.0


def _window_features(window: np.ndarray) -> np.ndarray:
    """Return the private, deterministic 41-value training feature vector."""

    result: list[float] = []
    for channel_index in range(len(CHANNELS)):
        values = window[:, channel_index].astype(np.float64, copy=False)
        valid = _finite_values(values)
        result.extend(
            (
                _median(values),
                float(np.std(valid)) if valid.size else 0.0,
                float(np.max(valid) - np.min(valid)) if valid.size else 0.0,
                _slope(values),
            )
        )

    vibration = window[:, 0].astype(np.float64, copy=False)
    valid_vibration = _finite_values(vibration)
    if valid_vibration.size:
        vibration_mean = float(valid_vibration.mean())
        vibration_rms = float(np.sqrt(np.mean(valid_vibration**2)))
        centered = valid_vibration - vibration_mean
        variance = float(np.mean(centered**2))
        kurtosis = float(np.mean(centered**4) / variance**2) if variance > 1e-12 else 0.0
        crest = float(np.max(np.abs(valid_vibration)) / max(vibration_rms, 1e-12))
        peak_to_peak = float(np.ptp(valid_vibration))
        demeaned = valid_vibration - vibration_mean
        spectrum = np.abs(np.fft.rfft(demeaned)) ** 2
        split = max(1, len(spectrum) // 4)
        band_ratio = float(spectrum[split:].sum() / max(spectrum[1:].sum(), 1e-12)) if len(spectrum) > 1 else 0.0
    else:
        vibration_rms = kurtosis = crest = peak_to_peak = band_ratio = 0.0
    result.extend((vibration_rms, kurtosis, crest, peak_to_peak, band_ratio))

    bearing = window[:, 2].astype(np.float64, copy=False)
    ambient = window[:, 7].astype(np.float64, copy=False)
    current = window[:, 5].astype(np.float64, copy=False)
    rpm = window[:, 6].astype(np.float64, copy=False)
    valid_delta = bearing - ambient
    valid_ratio = current / rpm
    result.extend(
        (
            _median(valid_delta),
            _median(valid_ratio[np.isfinite(valid_ratio) & (rpm > 0)]),
            _median(vibration - (vibration_mean if valid_vibration.size else 0.0)),
            float(np.isfinite(window).mean()),
        )
    )
    return np.asarray(result, dtype=np.float32)


def _load_events(data_root: Path) -> list[EventLabel]:
    database_url = f"sqlite:///{(data_root / 'plant.db').resolve()}"
    with session_scope(database_url) as session:
        rows = list(session.scalars(select(FaultEvent)))
    return [EventLabel(row.id, row.machine_id, row.mode, row.onset_ts, row.failure_ts or row.onset_ts) for row in rows]


def _load_sensor_frame(data_root: Path) -> pd.DataFrame:
    sensor_root = data_root / "sensors"
    if not sensor_root.exists():
        raise TrainingDataError(f"missing sensor data: {sensor_root}")
    columns = ["canonical_ts_ms", "machine_id", *CHANNELS]
    dataset = ds.dataset(sensor_root, format="parquet", partitioning="hive")
    missing = sorted(set(columns).difference(dataset.schema.names))
    if missing:
        raise TrainingDataError(f"sensor data missing columns: {missing}")
    frame = dataset.to_table(columns=columns).to_pandas()
    frame["machine_id"] = frame["machine_id"].astype(str)
    frame = frame.sort_values(["machine_id", "canonical_ts_ms"], kind="mergesort")
    frame = frame.drop_duplicates(["machine_id", "canonical_ts_ms"], keep="first").reset_index(drop=True)

    # The generated metadata identifies the intentionally Fahrenheit machines.
    # This local correction is the training substitute for B-04's unit
    # harmonisation; the missing B-05/B-15 serving seams remain untouched.
    metadata = json.loads((data_root / "metadata.json").read_text(encoding="utf-8"))
    fahrenheit = set(metadata.get("defects", {}).get("fahrenheit_machines", []))
    mask = frame["machine_id"].isin(fahrenheit)
    frame.loc[mask, "temp_bearing_c"] = (frame.loc[mask, "temp_bearing_c"] - 32.0) * (5.0 / 9.0)
    for channel in CHANNELS:
        frame[channel] = frame.groupby("machine_id", sort=False)[channel].transform(
            lambda values: values.interpolate(limit=10, limit_direction="both")
        )
    return frame


def _future_event(events: Iterable[EventLabel], machine_id: str, window_end: int) -> EventLabel | None:
    active = [
        event
        for event in events
        if event.machine_id == machine_id and event.onset_ts <= window_end < event.failure_ts
    ]
    if active:
        return min(active, key=lambda event: event.failure_ts)
    candidates = [
        event
        for event in events
        if event.machine_id == machine_id and window_end < event.failure_ts <= window_end + HORIZON_MS
    ]
    return min(candidates, key=lambda event: event.failure_ts, default=None)


def _inside_repair(events: Iterable[EventLabel], machine_id: str, window_end: int) -> bool:
    """Return whether a window is inside a known repair interval.

    A-03 contains fault degradation intervals, not repair records.  Its
    ``onset_ts`` to ``failure_ts`` span is therefore valid predictive signal
    and must not be mistaken for an active repair.  B-06 will provide repair
    history; until then this task-local substitute has no repair intervals to
    exclude.
    """

    del events, machine_id, window_end
    return False


def _build_windows(frame: pd.DataFrame, events: list[EventLabel]) -> WindowDataset:
    feature_rows: list[np.ndarray] = []
    machine_ids: list[str] = []
    window_ends: list[int] = []
    failure_labels: list[int] = []
    mode_labels: list[str] = []
    rul_days: list[float] = []
    target_event_ids: list[int] = []

    for machine_id, group in frame.groupby("machine_id", sort=True):
        group = group.sort_values("canonical_ts_ms", kind="mergesort")
        timestamps = group["canonical_ts_ms"].to_numpy(dtype=np.int64)
        values = group[list(CHANNELS)].to_numpy(dtype=np.float32)
        if len(timestamps) < 2:
            continue
        first_end = int(timestamps[0] + WINDOW_MS)
        last_end = int(timestamps[-1])
        for end in range(first_end, last_end + 1, STRIDE_MS):
            right = int(np.searchsorted(timestamps, end, side="right"))
            left = int(np.searchsorted(timestamps, end - WINDOW_MS, side="right"))
            if right - left < 0.90 * (WINDOW_MS // (30 * 1000)):
                continue
            target = _future_event(events, str(machine_id), end)
            if _inside_repair(events, str(machine_id), end):
                continue
            window = values[left:right]
            feature_rows.append(_window_features(window))
            machine_ids.append(str(machine_id))
            window_ends.append(end)
            failure_labels.append(int(target is not None))
            mode_labels.append(target.mode if target else "")
            rul_days.append(float((target.failure_ts - end) / DAY_MS) if target else float(min(60.0, max(1.0, (last_end - end) / DAY_MS))))
            target_event_ids.append(target.event_id if target else -1)

    if not feature_rows:
        raise TrainingDataError("no six-hour training windows were constructed")
    return WindowDataset(
        np.vstack(feature_rows).astype(np.float32),
        np.asarray(machine_ids, dtype=object),
        np.asarray(window_ends, dtype=np.int64),
        np.asarray(failure_labels, dtype=np.int8),
        np.asarray(mode_labels, dtype=object),
        np.asarray(rul_days, dtype=np.float32),
        np.asarray(target_event_ids, dtype=np.int64),
    )


def _split_windows(dataset: WindowDataset, metadata: dict[str, Any], events: list[EventLabel]) -> Split:
    base_ts = int(metadata["base_ts_ms"])
    test_start = base_ts + int(metadata["days"] * 0.80) * DAY_MS
    validation_start = base_ts + int(metadata["days"] * 0.64) * DAY_MS
    machine_ids = set(dataset.machine_ids.tolist())
    missing_unseen = set(UNSEEN_MACHINES).difference(machine_ids)
    if missing_unseen:
        raise TrainingDataError(f"configured unseen machines are missing from data: {sorted(missing_unseen)}")

    test_mask = np.isin(dataset.machine_ids, UNSEEN_MACHINES) | (dataset.window_ends >= test_start)
    validation_mask = (~test_mask) & (dataset.window_ends >= validation_start)
    train_mask = (~test_mask) & (dataset.window_ends < validation_start)
    if not train_mask.any() or not validation_mask.any() or not test_mask.any():
        raise TrainingDataError("time/machine split produced an empty partition")

    train_machines = set(dataset.machine_ids[train_mask].tolist())
    if train_machines.intersection(UNSEEN_MACHINES):
        raise TrainingDataError("unseen machine leakage detected in training split")

    heldout_events = [event for event in events if event.machine_id in UNSEEN_MACHINES or event.failure_ts >= test_start]
    counts = {mode: sum(event.mode == mode for event in heldout_events) for mode in FAILURE_MODES}
    if len(heldout_events) < 15 or any(count < 3 for count in counts.values()):
        raise TrainingDataError(f"held-out event contract failed: total={len(heldout_events)}, per_mode={counts}")
    print(f"held-out machines (never in training): {', '.join(UNSEEN_MACHINES)}")
    print(f"held-out failure events: {len(heldout_events)}; per mode: {counts}")
    print(f"split rows: train={int(train_mask.sum())}, validation={int(validation_mask.sum())}, test={int(test_mask.sum())}")
    return Split(np.flatnonzero(train_mask), np.flatnonzero(validation_mask), np.flatnonzero(test_mask))


def _impute(features: np.ndarray, train_indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(features, dtype=np.float64)
    medians = np.nanmedian(np.where(np.isfinite(values[train_indices]), values[train_indices], np.nan), axis=0)
    medians = np.nan_to_num(medians, nan=0.0)
    result = np.where(np.isfinite(values), values, medians)
    return result.astype(np.float32), medians.astype(np.float32)


def _tune_threshold(y_true: np.ndarray, probabilities: np.ndarray, min_precision: float) -> tuple[float, dict[str, float]]:
    candidates = np.unique(np.clip(np.r_[probabilities, np.linspace(0.0, 1.0, 201)], 0.0, 1.0))
    feasible: list[tuple[float, float, float]] = []
    for threshold in candidates:
        predicted = probabilities >= threshold
        precision = precision_score(y_true, predicted, zero_division=0)
        f1 = f1_score(y_true, predicted, zero_division=0)
        if precision >= min_precision:
            feasible.append((f1, precision, float(threshold)))
    if not feasible:
        raise TrainingDataError(f"no threshold satisfies validation precision >= {min_precision:.2f}")
    f1, precision, threshold = max(feasible, key=lambda item: (item[0], item[1], item[2]))
    return threshold, {"precision": float(precision), "f1": float(f1)}


def _metric_table(y_true: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, float]:
    predicted = probabilities >= threshold
    return {
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, predicted)),
        "pr_auc": float(average_precision_score(y_true, probabilities)) if len(np.unique(y_true)) > 1 else 0.0,
    }


def _event_detection(
    dataset: WindowDataset,
    indices: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
    events: list[EventLabel],
    test_start_ts: int,
) -> tuple[int, int]:
    detected = 0
    heldout_events = [event for event in events if event.machine_id in UNSEEN_MACHINES or event.failure_ts >= test_start_ts]
    # Use the exact held-out event rule from the split, rather than assuming
    # every machine has the same event schedule.
    for event in heldout_events:
        row_positions = [position for position, global_index in enumerate(indices) if dataset.machine_ids[global_index] == event.machine_id and event.failure_ts - HORIZON_MS <= dataset.window_ends[global_index] < event.failure_ts]
        if row_positions and bool(np.any(probabilities[row_positions] >= threshold)):
            detected += 1
    return detected, len(heldout_events)


def _mode_report(model: RandomForestClassifier, features: np.ndarray, dataset: WindowDataset, indices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    positive = indices[dataset.mode_labels[indices] != ""]
    if not len(positive):
        raise TrainingDataError("held-out split contains no labelled fault-mode windows")
    predicted = model.predict(features[positive])
    actual = dataset.mode_labels[positive]
    return np.asarray(actual, dtype=str), np.asarray(predicted, dtype=str)


def _drift_decline_rate(data_root: Path, dataset: WindowDataset, features: np.ndarray) -> float:
    drift_path = data_root / "drift_intervals.parquet"
    if not drift_path.exists():
        return 0.0
    drift = pd.read_parquet(drift_path)
    pressure_index = FEATURE_NAMES.index("pressure_bar_mean")
    bearing_index = FEATURE_NAMES.index("temp_bearing_c_mean")
    detected = 0
    for row in drift.itertuples(index=False):
        mask = (dataset.machine_ids == row.machine_id) & (dataset.window_ends >= int(row.onset_ts)) & (dataset.window_ends <= int(row.end_ts))
        baseline_mask = (dataset.machine_ids == row.machine_id) & (dataset.window_ends < int(row.onset_ts)) & (dataset.window_ends >= int(row.onset_ts) - WINDOW_MS)
        if not mask.any() or not baseline_mask.any():
            continue
        pressure_shift = float(np.nanmedian(features[mask, pressure_index]) - np.nanmedian(features[baseline_mask, pressure_index]))
        bearing_shift = float(np.nanmedian(features[mask, bearing_index]) - np.nanmedian(features[baseline_mask, bearing_index]))
        detected += int(pressure_shift >= DRIFT_PRESSURE_SHIFT or bearing_shift >= DRIFT_BEARING_SHIFT)
    return float(detected / len(drift)) if len(drift) else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_artifacts(artifact_dir: Path, artifacts: dict[str, Any], manifest: dict[str, Any]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".train-", dir=artifact_dir) as temporary:
        temporary_dir = Path(temporary)
        written: dict[str, str] = {}
        for name, value in artifacts.items():
            filename = f"{name}.joblib"
            temporary_path = temporary_dir / filename
            joblib.dump(value, temporary_path, compress=3)
            final_path = artifact_dir / filename
            os.replace(temporary_path, final_path)
            written[filename] = _sha256(final_path)
        manifest["artifacts"] = written
        manifest_path = artifact_dir / "MANIFEST.json"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=artifact_dir, delete=False) as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary_manifest = Path(handle.name)
        os.replace(temporary_manifest, manifest_path)


def train(data_root: str | Path | None = None, artifact_dir: str | Path | None = None) -> dict[str, Any]:
    """Train all A-04 model families and return the persisted manifest."""

    data_path = Path(data_root) if data_root is not None else ROOT / "data"
    output_path = Path(artifact_dir) if artifact_dir is not None else ROOT / settings.predict.artifacts_dir
    metadata_path = data_path / "metadata.json"
    if not metadata_path.exists():
        raise TrainingDataError(f"missing generator metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    events = _load_events(data_path)
    raw_frame = _load_sensor_frame(data_path)
    dataset = _build_windows(raw_frame, events)
    split = _split_windows(dataset, metadata, events)
    model_features, medians = _impute(dataset.features, split.train)

    x_train = model_features[split.train]
    x_validation = model_features[split.validation]
    x_test = model_features[split.test]
    y_train = dataset.failure_labels[split.train]
    y_validation = dataset.failure_labels[split.validation]
    y_test = dataset.failure_labels[split.test]
    if len(np.unique(y_train)) < 2 or len(np.unique(y_validation)) < 2 or len(np.unique(y_test)) < 2:
        raise TrainingDataError("failure labels must contain both classes in train, validation, and test")

    failure_base = HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.08,
        max_leaf_nodes=15,
        l2_regularization=0.1,
        random_state=SEED,
    )
    failure_model = CalibratedClassifierCV(estimator=failure_base, method="isotonic", cv=3)
    failure_model.fit(x_train, y_train)
    validation_probabilities = failure_model.predict_proba(x_validation)[:, 1]
    tuned_tau, _ = _tune_threshold(y_validation, validation_probabilities, settings.predict.min_precision)
    default_tau = float(settings.predict.threshold_tau_default)
    candidate_tau = min(tuned_tau, default_tau)
    candidate_metrics = _metric_table(y_validation, validation_probabilities, candidate_tau)
    if candidate_metrics["precision"] >= settings.predict.min_precision:
        threshold_tau = candidate_tau
        validation_threshold_metrics = candidate_metrics
    else:
        threshold_tau = tuned_tau
        validation_threshold_metrics = _metric_table(y_validation, validation_probabilities, tuned_tau)
    test_probabilities = failure_model.predict_proba(x_test)[:, 1]
    test_metrics = _metric_table(y_test, test_probabilities, threshold_tau)
    test_start_ts = int(metadata["base_ts_ms"] + int(metadata["days"] * 0.80) * DAY_MS)
    event_detected, event_total = _event_detection(dataset, split.test, test_probabilities, threshold_tau, events, test_start_ts)

    mode_train = split.train[dataset.mode_labels[split.train] != ""]
    mode_model = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced_subsample",
        random_state=SEED,
        n_jobs=-1,
    )
    mode_model.fit(model_features[mode_train], dataset.mode_labels[mode_train])
    mode_actual, mode_predicted = _mode_report(mode_model, model_features, dataset, split.test)
    mode_matrix = confusion_matrix(mode_actual, mode_predicted, labels=list(FAILURE_MODES)).tolist()
    mode_balanced_accuracy = float(balanced_accuracy_score(mode_actual, mode_predicted))

    rul_models: dict[str, GradientBoostingRegressor] = {}
    rul_predictions: list[np.ndarray] = []
    for quantile in (0.1, 0.5, 0.9):
        name = f"rul_p{int(quantile * 100):02d}"
        model = GradientBoostingRegressor(
            loss="quantile",
            alpha=quantile,
            n_estimators=120,
            learning_rate=0.05,
            max_depth=2,
            min_samples_leaf=5,
            random_state=SEED,
        )
        model.fit(x_train, dataset.rul_days[split.train])
        rul_models[name] = model
        rul_predictions.append(model.predict(x_test))
    raw_rul = np.vstack(rul_predictions)
    raw_rul_ordering_rate = float(np.mean((raw_rul[0] <= raw_rul[1]) & (raw_rul[1] <= raw_rul[2])))
    ordered_rul_models = tuple(rul_models[name] for name in ("rul_p10", "rul_p50", "rul_p90"))
    ordered_rul_predictions = np.vstack(
        [OrderedQuantileRegressor(ordered_rul_models, position).predict(x_test) for position in range(3)]
    )
    rul_p10, rul_p50, rul_p90 = ordered_rul_predictions
    rul_ordering_rate = float(np.mean((rul_p10 <= rul_p50) & (rul_p50 <= rul_p90)))

    if len(np.unique(y_validation)) > 1:
        importance = permutation_importance(
            failure_model,
            x_validation,
            y_validation,
            scoring="average_precision",
            n_repeats=3,
            random_state=SEED,
            n_jobs=-1,
        )
        permutation_payload = {
            "feature_names": list(FEATURE_NAMES),
            "importance_mean": importance.importances_mean.astype(float).tolist(),
            "importance_std": importance.importances_std.astype(float).tolist(),
        }
    else:
        permutation_payload = {"feature_names": list(FEATURE_NAMES), "importance_mean": [0.0] * len(FEATURE_NAMES), "importance_std": [0.0] * len(FEATURE_NAMES)}

    drift_rate = _drift_decline_rate(data_path, dataset, dataset.features)
    confusion = confusion_matrix(y_test, test_probabilities >= threshold_tau, labels=[0, 1]).tolist()
    manifest: dict[str, Any] = {
        "feature_version": FEATURE_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "training_rows": int(len(split.train)),
        "validation_rows": int(len(split.validation)),
        "heldout_rows": int(len(split.test)),
        "seed": SEED,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "threshold_tau": float(threshold_tau),
        "threshold_constraint": {"min_precision": settings.predict.min_precision, "validation": validation_threshold_metrics},
        "split": {
            "unseen_machines": list(UNSEEN_MACHINES),
            "test_start_ts": int(metadata["base_ts_ms"] + int(metadata["days"] * 0.80) * DAY_MS),
            "train_machines": sorted(set(dataset.machine_ids[split.train].tolist())),
        },
        "metrics": {
            "per_event_detection": {"detected": event_detected, "total": event_total, "rate": float(event_detected / event_total) if event_total else 0.0},
            "failure": {**test_metrics, "confusion_matrix": confusion},
            "fault_mode": {"labels": list(FAILURE_MODES), "balanced_accuracy": mode_balanced_accuracy, "confusion_matrix": mode_matrix},
            "sensor_drift_decline_rate": drift_rate,
            "rul_quantile_ordering_rate": rul_ordering_rate,
            "rul_raw_quantile_ordering_rate": raw_rul_ordering_rate,
        },
        "temporary_substitutes": ["window_labelling", "feature_extraction", "parquet_window_reader"],
        "rul_quantile_ordering": "OrderedQuantileRegressor wrapper persisted in each RUL artifact",
    }
    artifacts: dict[str, Any] = {
        "failure_calibrated": failure_model,
        "fault_mode": mode_model,
        "rul_p10": OrderedQuantileRegressor(ordered_rul_models, 0),
        "rul_p50": OrderedQuantileRegressor(ordered_rul_models, 1),
        "rul_p90": OrderedQuantileRegressor(ordered_rul_models, 2),
        "feature_medians": medians,
        "permutation_importance": permutation_payload,
    }
    _write_artifacts(output_path, artifacts, manifest)

    print("\nA-04 metrics (held-out test set)")
    print(f"per-event detection: {event_detected}/{event_total}")
    print(f"balanced accuracy:   {test_metrics['balanced_accuracy']:.4f}")
    print(f"precision:            {test_metrics['precision']:.4f}")
    print(f"recall:               {test_metrics['recall']:.4f}")
    print(f"F1:                   {test_metrics['f1']:.4f}")
    print(f"PR-AUC:               {test_metrics['pr_auc']:.4f}")
    print("5-class fault-mode confusion matrix (bearing, rotor, seal, overheat, lubrication):")
    for row in mode_matrix:
        print("  " + " ".join(f"{value:4d}" for value in row))
    print(f"sensor_drift decline-rate: {drift_rate:.4f}")
    print(f"RUL p10 <= p50 <= p90: {rul_ordering_rate:.1%} of held-out windows")
    print(f"tuned threshold_tau: {threshold_tau:.6f} (validation precision {validation_threshold_metrics['precision']:.4f})")
    print(f"manifest: {output_path / 'MANIFEST.json'}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / settings.predict.artifacts_dir)
    args = parser.parse_args()
    train(args.data_root, args.artifacts_dir)


if __name__ == "__main__":
    main()
