"""Generate and verify the deterministic SentinelPdM demonstration plant.

The generator deliberately emits imperfect source data.  The extra source
timestamp and clock-offset columns make the timestamp defect observable while
leaving ``ts_utc_ms`` as the raw sensor timestamp consumed by preprocessing.
Ground-truth labels for the non-failure drift intervals live in a separate
partition because they are not rows in ``fault_events``.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from sqlalchemy import select

# Support both ``python -m data.generate`` and the task's documented
# ``python data/generate.py`` invocation when launched from the repository.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, settings
from core.db import create_schema, get_engine, session_scope
from core.models import FaultEvent, Machine


CHANNELS = (
    "vibration_rms_mm_s",
    "vibration_kurtosis",
    "temp_bearing_c",
    "temp_motor_c",
    "pressure_bar",
    "current_a",
    "rpm",
    "ambient_temp_c",
)
BASE_TS_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
DAY_MS = 24 * 60 * 60 * 1000
SAMPLE_MS = 30 * 1000
MACHINE_IDS = tuple(
    [f"PUMP-{index:02d}" for index in range(1, 8)]
    + [f"PRESS-{index:02d}" for index in range(1, 4)]
    + [f"CNC-{index:02d}" for index in range(9, 12)]
    + [f"CONV-{index:02d}" for index in range(13, 15)]
)
FAILURE_MODES = (
    "bearing_wear",
    "rotor_imbalance",
    "seal_leak",
    "motor_overheat",
    "lubrication_degradation",
)
ALL_MODES = FAILURE_MODES + ("sensor_drift",)
FAHRENHEIT_MACHINES = frozenset({"PRESS-03", "CONV-14"})
FROZEN_MACHINE = "CNC-09"
GATEWAY_GAP_MACHINE = "CONV-14"


# The single source of truth for injected fault physics.  Values are additive
# channel effects per unit degradation (except vibration_rms, which is a
# multiplicative ramp for bearing wear).
FAULT_SIGNATURES: dict[str, dict[str, Any]] = {
    "bearing_wear": {
        "duration_days": 3.0,
        "effects": {"vibration_kurtosis": 2.2, "temp_bearing_c": 7.0},
        "vibration_multiplier": 5.0,
    },
    "rotor_imbalance": {
        "duration_days": 4.0,
        "effects": {"vibration_rms_mm_s": 2.0, "vibration_kurtosis": 0.8},
        "vibration_multiplier": 0.0,
    },
    "seal_leak": {
        "duration_days": 5.0,
        "effects": {"pressure_bar": -1.5, "current_a": -2.0, "temp_motor_c": 8.0},
        "vibration_multiplier": 0.0,
    },
    "motor_overheat": {
        "duration_days": 2.0,
        "effects": {"temp_motor_c": 20.0, "current_a": 4.0},
        "vibration_multiplier": 0.0,
    },
    "lubrication_degradation": {
        "duration_days": 12.0,
        "effects": {"vibration_rms_mm_s": 1.8, "temp_bearing_c": 6.0},
        "vibration_multiplier": 0.0,
    },
    "sensor_drift": {
        "duration_days": 0.5,
        "effects": {"pressure_bar": 1.8, "temp_bearing_c": 5.0},
        "vibration_multiplier": 0.0,
    },
}


class DemoIntegrityError(RuntimeError):
    """Raised when generated data cannot support the planned demonstration."""


@dataclass(frozen=True)
class Scenario:
    name: str
    machine_id: str
    mode: str
    onset_ts: int | None
    expected: str


@dataclass(frozen=True)
class GenerationReport:
    output_root: str
    sensor_root: str
    database_path: str
    t0_ts_ms: int
    row_count: int
    cycle_count: int
    fault_event_count: int
    drift_interval_count: int
    sample_partition: str
    parquet_partition_count: int


def _machine_metadata() -> list[dict[str, Any]]:
    models = {"PUMP": "PX-400", "PRESS": "HP-220", "CNC": "M-5AX", "CONV": "CD-90"}
    types = {"PUMP": "industrial_pump", "PRESS": "hydraulic_press", "CNC": "cnc_mill", "CONV": "conveyor_drive"}
    rows: list[dict[str, Any]] = []
    for index, machine_id in enumerate(MACHINE_IDS):
        prefix = machine_id.split("-", 1)[0]
        line = f"LINE-{index % 3 + 1}"
        rated_rpm = {"PUMP": 1_800, "PRESS": 1_450, "CNC": 3_600, "CONV": 1_200}[prefix]
        rows.append(
            {
                "id": machine_id,
                "name": f"{prefix.title()} asset {machine_id.rsplit('-', 1)[1]}",
                "type": types[prefix],
                "model": models[prefix],
                "line": line,
                "install_date": BASE_TS_MS - (730 + index * 19) * DAY_MS,
                "criticality": 5 if index % 5 == 0 else (4 if index % 2 else 3),
                "rated_rpm": rated_rpm,
                "hourly_downtime_cost_gbp": settings.plant.hourly_downtime_cost_default_gbp + index * 125,
                "unit_map": {"temp_bearing_c": "F"} if machine_id in FAHRENHEIT_MACHINES else {"temp_bearing_c": "C"},
            }
        )
    return rows


def _scenario_t0() -> int:
    return BASE_TS_MS + int(settings.stream.replay_start_day * DAY_MS)


def _event_rows(t0_ts_ms: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    eligible = [machine_id for machine_id in MACHINE_IDS if machine_id != "PRESS-03"]
    for mode_index, mode in enumerate(FAILURE_MODES):
        duration_ms = int(FAULT_SIGNATURES[mode]["duration_days"] * DAY_MS)
        for event_index in range(12):
            if mode == "bearing_wear" and event_index == 0:
                machine_id = "PUMP-07"
                onset_ts = t0_ts_ms + int(7.5 * 60 * 60 * 1000)
            elif mode == "seal_leak" and event_index == 0:
                machine_id = "CNC-11"
                onset_ts = t0_ts_ms + 1 * DAY_MS
            else:
                machine_id = eligible[(mode_index * 5 + event_index * 3) % len(eligible)]
                day = (mode_index * 9 + event_index * 4 + 2) % 52
                onset_ts = BASE_TS_MS + day * DAY_MS + (6 + event_index % 8) * 60 * 60 * 1000
            failure_ts = onset_ts + duration_ms
            rows.append(
                {
                    "machine_id": machine_id,
                    "mode": mode,
                    "onset_ts": onset_ts,
                    "failure_ts": failure_ts,
                    "severity": ("critical" if event_index % 4 == 0 else "high" if event_index % 3 == 0 else "medium"),
                    "actual_downtime_min": 45 + mode_index * 15 + event_index * 3,
                }
            )
    return rows


def _drift_rows(t0_ts_ms: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(settings.data.drift_intervals):
        if index == 0:
            machine_id = "PRESS-03"
            onset_ts = t0_ts_ms + 2 * DAY_MS
        else:
            machine_id = MACHINE_IDS[(index * 7 + 2) % len(MACHINE_IDS)]
            onset_ts = BASE_TS_MS + (index * 4 + 3) * DAY_MS + 3 * 60 * 60 * 1000
        rows.append(
            {
                "interval_id": f"DRIFT-{index + 1:02d}",
                "machine_id": machine_id,
                "mode": "sensor_drift",
                "onset_ts": onset_ts,
                "end_ts": onset_ts + int(FAULT_SIGNATURES["sensor_drift"]["duration_days"] * DAY_MS),
                "failure_event_id": None,
                "label": "non_failure",
            }
        )
    return rows


def _global_index(machine_index: int, day: int, sample: int, samples_per_day: int) -> int:
    return machine_index * settings.data.days * samples_per_day + day * samples_per_day + sample


def _build_defect_masks(total_rows: int, samples_per_day: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    rng = np.random.default_rng(settings.data.seed)
    gateway_day = 57
    gateway_start = _global_index(MACHINE_IDS.index(GATEWAY_GAP_MACHINE), gateway_day, 6 * 120, samples_per_day)
    gateway_count = 8 * 60 * 2
    gateway_mask = np.zeros(total_rows, dtype=bool)
    gateway_mask[gateway_start : gateway_start + gateway_count] = True

    target_dropouts = int(round(total_rows * 0.011))
    dropout_mask = gateway_mask.copy()
    while int(dropout_mask.sum()) < target_dropouts:
        start = int(rng.integers(0, total_rows))
        length = int(rng.integers(1, 21))
        end = min(start + length, total_rows)
        if gateway_mask[start:end].any():
            continue
        dropout_mask[start:end] = True

    spike_target = int(round(total_rows * 0.004))
    candidates = np.flatnonzero(~dropout_mask)
    spike_indices = rng.choice(candidates, size=spike_target, replace=False)
    spike_mask = np.zeros(total_rows, dtype=bool)
    spike_mask[spike_indices] = True

    duplicate_target = int(round(total_rows * 0.006))
    duplicate_candidates = np.flatnonzero(~dropout_mask)
    duplicate_candidates = duplicate_candidates[duplicate_candidates % samples_per_day != 0]
    duplicate_indices = rng.choice(duplicate_candidates, size=duplicate_target, replace=False)
    duplicate_mask = np.zeros(total_rows, dtype=bool)
    duplicate_mask[duplicate_indices] = True
    return dropout_mask, spike_mask, duplicate_mask, gateway_start, gateway_count


def _apply_signature(values: dict[str, np.ndarray], mode: str, progress: np.ndarray) -> None:
    signature = FAULT_SIGNATURES[mode]
    for channel, amplitude in signature["effects"].items():
        values[channel] += np.float32(amplitude) * progress
    multiplier = float(signature.get("vibration_multiplier", 0.0))
    if multiplier:
        values["vibration_rms_mm_s"] *= 1.0 + multiplier * progress**2


def _make_day(
    machine: dict[str, Any],
    machine_index: int,
    day: int,
    samples_per_day: int,
    rng: np.random.Generator,
    events: Iterable[dict[str, Any]],
    drift_intervals: Iterable[dict[str, Any]],
    dropout_mask: np.ndarray,
    spike_mask: np.ndarray,
    duplicate_mask: np.ndarray,
) -> dict[str, np.ndarray]:
    ideal_ts = BASE_TS_MS + (day * samples_per_day + np.arange(samples_per_day)) * SAMPLE_MS
    source_offset_s = (-90 if machine_index % 3 == 0 else 90 if machine_index % 3 == 1 else 0)
    ts_utc = ideal_ts + source_offset_s * 1000
    phase = (np.arange(samples_per_day) % 360) / 360.0
    load = np.clip(np.sin(phase * math.pi) ** 0.7, 0.0, 1.0).astype(np.float32)
    rated_rpm = machine["rated_rpm"]
    values = {
        "vibration_rms_mm_s": (2.2 + 0.35 * load + rng.normal(0, 0.08, samples_per_day)).astype(np.float32),
        "vibration_kurtosis": (3.0 + 0.18 * load + rng.normal(0, 0.04, samples_per_day)).astype(np.float32),
        "temp_bearing_c": (54.0 + 6.0 * load + rng.normal(0, 0.25, samples_per_day)).astype(np.float32),
        "temp_motor_c": (49.0 + 9.0 * load + rng.normal(0, 0.3, samples_per_day)).astype(np.float32),
        "pressure_bar": (6.0 + 0.25 * load + rng.normal(0, 0.025, samples_per_day)).astype(np.float32),
        "current_a": (10.0 + 4.0 * load + rng.normal(0, 0.12, samples_per_day)).astype(np.float32),
        "rpm": (rated_rpm * (0.35 + 0.65 * load) + rng.normal(0, 4.0, samples_per_day)).astype(np.float32),
        "ambient_temp_c": (18.0 + 3.0 * np.sin(np.arange(samples_per_day) / samples_per_day * 2 * math.pi) + rng.normal(0, 0.15, samples_per_day)).astype(np.float32),
    }
    start_ts = ideal_ts[0]
    end_ts = ideal_ts[-1] + SAMPLE_MS
    for event in events:
        if event["machine_id"] != machine["id"] or event["failure_ts"] <= start_ts or event["onset_ts"] >= end_ts:
            continue
        progress = np.clip((ideal_ts - event["onset_ts"]) / (event["failure_ts"] - event["onset_ts"]), 0.0, 1.0).astype(np.float32)
        _apply_signature(values, event["mode"], progress)
    for interval in drift_intervals:
        if interval["machine_id"] != machine["id"] or interval["end_ts"] <= start_ts or interval["onset_ts"] >= end_ts:
            continue
        progress = np.clip((ideal_ts - interval["onset_ts"]) / (interval["end_ts"] - interval["onset_ts"]), 0.0, 1.0).astype(np.float32)
        _apply_signature(values, "sensor_drift", progress)

    global_start = _global_index(machine_index, day, 0, samples_per_day)
    local_dropout = dropout_mask[global_start : global_start + samples_per_day]
    local_spike = spike_mask[global_start : global_start + samples_per_day]
    local_duplicate = duplicate_mask[global_start : global_start + samples_per_day]
    spike_noise = rng.uniform(20.0, 40.0, samples_per_day).astype(np.float32)
    values["vibration_rms_mm_s"][local_spike] += spike_noise[local_spike]

    if machine["id"] in FAHRENHEIT_MACHINES:
        values["temp_bearing_c"] = values["temp_bearing_c"] * 9.0 / 5.0 + 32.0

    for channel in CHANNELS:
        values[channel][local_dropout] = np.nan
    # A near duplicate keeps the same source timestamp and differs only by a
    # small amount of sensor noise. It therefore remains discoverable without
    # adding a hidden ground-truth column to the modelling schema.
    for local_index in np.flatnonzero(local_duplicate):
        if local_index == 0 or local_dropout[local_index - 1]:
            continue
        ts_utc[local_index] = ts_utc[local_index - 1]
        for channel in CHANNELS:
            if np.isfinite(values[channel][local_index - 1]):
                values[channel][local_index] = values[channel][local_index - 1] * np.float32(1.0001)

    if machine["id"] == FROZEN_MACHINE and day == 30:
        frozen_start = 12 * 120
        frozen_end = frozen_start + 6 * 120
        values["pressure_bar"][frozen_start:frozen_end] = values["pressure_bar"][frozen_start]

    result = {"ts_utc_ms": ts_utc.astype(np.int64), "canonical_ts_ms": ideal_ts.astype(np.int64), "clock_offset_s": np.full(samples_per_day, source_offset_s, dtype=np.int16)}
    result.update(values)
    result["machine_id"] = np.full(samples_per_day, machine["id"], dtype=object)
    result["cycle_id"] = (day * 8 + np.arange(samples_per_day) // 360).astype(np.int32)
    result["is_simulated"] = np.ones(samples_per_day, dtype=bool)
    return result


def _write_parquet(path: Path, columns: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {
        "ts_utc_ms": columns["ts_utc_ms"],
        "canonical_ts_ms": columns["canonical_ts_ms"],
        "clock_offset_s": columns["clock_offset_s"],
        "machine_id": columns["machine_id"],
        **{channel: columns[channel] for channel in CHANNELS},
        "cycle_id": columns["cycle_id"],
        "is_simulated": columns["is_simulated"],
    }
    table = pa.Table.from_pydict(ordered)
    pq.write_table(table, path, compression="zstd", use_dictionary=False, data_page_version="1.0")


def _write_labels(output_root: Path, drift_intervals: list[dict[str, Any]], scenarios: list[Scenario]) -> None:
    pq.write_table(pa.Table.from_pylist(drift_intervals), output_root / "drift_intervals.parquet", compression="zstd", use_dictionary=False)
    scenario_payload = {
        "historical_fault_codes": {"CNC-11": "PMP-02"},
        "scenarios": [asdict(scenario) for scenario in scenarios],
    }
    (output_root / "scenario_manifest.json").write_text(json.dumps(scenario_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_demo_notes(output_root: Path, t0_ts_ms: int) -> None:
    t0 = datetime.fromtimestamp(t0_ts_ms / 1000, tz=timezone.utc).isoformat()
    notes = f"""# SentinelPdM demo notes

Generated deterministically with seed `{settings.data.seed}`. Replay origin `t0` is day 56
(`{t0}` UTC). These are the expected, model-free signal facts; the later model-integrity
check appends the measured alert crossing after training.

## Planted scenarios

- **PUMP-07**: `bearing_wear` begins about 7.5 plant-hours after `t0`. Vibration RMS ramps
  from its healthy baseline to more than four times baseline before the labelled failure.
- **PRESS-03**: `sensor_drift` raises a fault-shaped signal but has no failure event. It is
  the deliberate false-positive trap and should be handled as data-quality evidence.
- **CNC-11**: `seal_leak` is planted while its dominant historical code is `PMP-02`, so
  sensor evidence and maintenance history conflict.
- **CONV-14**: every sensor channel is unavailable for one contiguous eight-hour gateway
  outage. The correct behaviour is to withhold a prediction for that interval.

The generator's `verify_signal()` checks only persisted, model-free facts and raises
`DemoIntegrityError` when any fact is missing. It does not depend on a trained model.
"""
    (output_root / "DEMO_NOTES.md").write_text(notes, encoding="utf-8", newline="\n")


def _populate_database(database_url: str, machines: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    create_schema(database_url)
    with session_scope(database_url) as session:
        session.add_all([Machine(**machine) for machine in machines])
        session.flush()
        session.add_all([FaultEvent(**event) for event in events])


def generate(output_root: str | Path | None = None, seed: int | None = None) -> GenerationReport:
    """Generate the complete A-03 dataset and return its persisted inventory."""
    root = Path(output_root) if output_root is not None else ROOT / "data"
    root = root.resolve()
    sensor_root = root / "sensors"
    database_path = root / "plant.db"
    seed_value = settings.data.seed if seed is None else seed
    if seed_value != settings.data.seed:
        raise ValueError("A-03 uses the configured deterministic seed; update config.yaml instead")
    days = settings.data.days
    sample_seconds = settings.data.sample_seconds
    samples_per_day = 24 * 60 * 60 // sample_seconds
    total_rows = len(MACHINE_IDS) * days * samples_per_day
    root.mkdir(parents=True, exist_ok=True)
    if sensor_root.exists():
        shutil.rmtree(sensor_root)
    if database_path.exists():
        get_engine(f"sqlite:///{database_path}").dispose()
        database_path.unlink()
    for stale in (root / "drift_intervals.parquet", root / "scenario_manifest.json", root / "DEMO_NOTES.md", root / "metadata.json"):
        if stale.exists():
            stale.unlink()

    t0_ts_ms = _scenario_t0()
    machines = _machine_metadata()
    events = _event_rows(t0_ts_ms)
    drift_intervals = _drift_rows(t0_ts_ms)
    dropout_mask, spike_mask, duplicate_mask, gateway_start, gateway_count = _build_defect_masks(total_rows, samples_per_day)
    rng = np.random.default_rng(seed_value)
    sample_partition = ""
    for machine_index, machine in enumerate(machines):
        for day in range(days):
            columns = _make_day(machine, machine_index, day, samples_per_day, rng, events, drift_intervals, dropout_mask, spike_mask, duplicate_mask)
            partition_date = datetime.fromtimestamp(int(columns["canonical_ts_ms"][0]) / 1000, tz=timezone.utc).date().isoformat()
            path = sensor_root / f"machine_id={machine['id']}" / f"date={partition_date}.parquet"
            _write_parquet(path, columns)
            if not sample_partition:
                sample_partition = str(path)

    database_url = f"sqlite:///{database_path}"
    _populate_database(database_url, machines, events)
    scenarios = [
        Scenario("protagonist", "PUMP-07", "bearing_wear", t0_ts_ms + int(7.5 * 60 * 60 * 1000), "ramping vibration"),
        Scenario("false_positive_trap", "PRESS-03", "sensor_drift", drift_intervals[0]["onset_ts"], "drift without failure"),
        Scenario("conflicting_history", "CNC-11", "seal_leak", next(event["onset_ts"] for event in events if event["machine_id"] == "CNC-11"), "sensor/history conflict"),
        Scenario("gateway_outage", GATEWAY_GAP_MACHINE, "missing_data", None, "contiguous eight-hour gap"),
    ]
    _write_labels(root, drift_intervals, scenarios)
    _write_demo_notes(root, t0_ts_ms)
    metadata = {
        "generator": "A-03",
        "seed": seed_value,
        "base_ts_ms": BASE_TS_MS,
        "t0_ts_ms": t0_ts_ms,
        "days": days,
        "sample_seconds": sample_seconds,
        "channels": list(CHANNELS),
        "machine_count": len(machines),
        "line_count": len({machine["line"] for machine in machines}),
        "cycle_count": len(machines) * days * 8,
        "clock_offsets_s": sorted({-90, 0, 90}),
        "defects": {
            "spike_rate": int(spike_mask.sum()) / total_rows,
            "dropout_rate": int(dropout_mask.sum()) / total_rows,
            "near_duplicate_rate": int(duplicate_mask.sum()) / total_rows,
            "gateway_gap_rows": gateway_count,
            "frozen_machine": FROZEN_MACHINE,
            "fahrenheit_machines": sorted(FAHRENHEIT_MACHINES),
        },
        "sample_partition": sample_partition,
    }
    (root / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return GenerationReport(str(root), str(sensor_root), str(database_path), t0_ts_ms, total_rows, len(machines) * days * 8, len(events), len(drift_intervals), sample_partition, days * len(machines))


def _read_machine(sensor_root: Path, machine_id: str) -> Any:
    return ds.dataset(sensor_root, format="parquet", partitioning="hive").to_table(filter=ds.field("machine_id") == machine_id).to_pandas()


def verify_signal(output_root: str | Path | None = None) -> None:
    """Verify persisted A-03 facts without loading a model or model threshold."""
    root = Path(output_root) if output_root is not None else ROOT / "data"
    sensor_root = root / "sensors"
    metadata_path = root / "metadata.json"
    database_url = f"sqlite:///{(root / 'plant.db').resolve()}"
    if not sensor_root.exists() or not metadata_path.exists():
        raise DemoIntegrityError(f"missing generated dataset under {root}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    dataset = ds.dataset(sensor_root, format="parquet", partitioning="hive")
    required_columns = {"ts_utc_ms", "machine_id", *CHANNELS, "cycle_id", "is_simulated"}
    if not required_columns.issubset(set(dataset.schema.names)):
        missing_columns = sorted(required_columns.difference(dataset.schema.names))
        raise DemoIntegrityError(f"sensor schema is missing columns: {missing_columns}")
    row_count = dataset.count_rows()
    expected_rows = metadata["machine_count"] * metadata["days"] * (24 * 60 * 60 // metadata["sample_seconds"])
    if row_count < 2_550_000 or row_count != expected_rows:
        raise DemoIntegrityError(f"unexpected sensor row count: {row_count}")

    table = dataset.to_table(columns=list(CHANNELS))
    frame = table.to_pandas()
    dropout_rate = frame[list(CHANNELS)].isna().all(axis=1).mean()
    spike_rate = (frame["vibration_rms_mm_s"] > 20.0).mean()
    if not 0.010 <= dropout_rate <= 0.012:
        raise DemoIntegrityError(f"dropout rate outside contract: {dropout_rate:.4%}")
    if not 0.003 <= spike_rate <= 0.005:
        raise DemoIntegrityError(f"spike rate outside contract: {spike_rate:.4%}")
    if set(metadata.get("clock_offsets_s", [])) != {-90, 0, 90}:
        raise DemoIntegrityError("per-source clock skew is not represented")

    with session_scope(database_url) as session:
        machines = list(session.scalars(select(Machine)))
        events = list(session.scalars(select(FaultEvent)))
    if len(machines) != 15 or len({machine.line for machine in machines}) != 3:
        raise DemoIntegrityError("machine or line inventory is incomplete")
    if len(events) != 60 or any(sum(event.mode == mode for event in events) < 10 for mode in FAILURE_MODES):
        raise DemoIntegrityError("fault-event inventory is incomplete")
    drift_table = pq.read_table(root / "drift_intervals.parquet").to_pandas()
    if len(drift_table) != 12 or drift_table["failure_event_id"].notna().any():
        raise DemoIntegrityError("sensor-drift labels are not non-failure intervals")
    if not {machine.id for machine in machines if machine.unit_map.get("temp_bearing_c") == "F"} == FAHRENHEIT_MACHINES:
        raise DemoIntegrityError("Fahrenheit unit metadata is incomplete")

    pump = _read_machine(sensor_root, "PUMP-07")
    pump_event = next(event for event in events if event.machine_id == "PUMP-07" and event.mode == "bearing_wear")
    t0_ts_ms = int(metadata["t0_ts_ms"])
    if abs(pump_event.onset_ts - (t0_ts_ms + int(7.5 * 60 * 60 * 1000))) > 15 * 60 * 1000:
        raise DemoIntegrityError("PUMP-07 onset is not in the planned window")
    baseline = float(pump.loc[pump["canonical_ts_ms"] < pump_event.onset_ts, "vibration_rms_mm_s"].median())
    before_failure = pump.loc[(pump["canonical_ts_ms"] >= pump_event.onset_ts) & (pump["canonical_ts_ms"] < pump_event.failure_ts), "vibration_rms_mm_s"]
    if not math.isfinite(baseline) or float(before_failure.max()) < 4.0 * baseline:
        raise DemoIntegrityError("PUMP-07 vibration does not ramp fourfold before failure")

    press = _read_machine(sensor_root, "PRESS-03")
    drift = drift_table[drift_table["machine_id"] == "PRESS-03"].iloc[0]
    press_before = press.loc[press["canonical_ts_ms"] < drift["onset_ts"], "pressure_bar"].median()
    press_during = press.loc[(press["canonical_ts_ms"] >= drift["onset_ts"]) & (press["canonical_ts_ms"] < drift["end_ts"]), "pressure_bar"].median()
    if not math.isfinite(float(press_during)) or float(press_during - press_before) < 0.5 or any(event.machine_id == "PRESS-03" for event in events):
        raise DemoIntegrityError("PRESS-03 drift scenario is not a non-failure signature")

    cnc_event = next(event for event in events if event.machine_id == "CNC-11" and event.mode == "seal_leak")
    historical_code = json.loads((root / "scenario_manifest.json").read_text(encoding="utf-8"))["historical_fault_codes"]["CNC-11"]
    if historical_code == cnc_event.mode:
        raise DemoIntegrityError("CNC-11 history does not conflict with planted mode")

    conv = _read_machine(sensor_root, GATEWAY_GAP_MACHINE)
    missing = conv[list(CHANNELS)].isna().all(axis=1).to_numpy()
    longest = current = 0
    for is_missing in missing:
        current = current + 1 if is_missing else 0
        longest = max(longest, current)
    if longest < 8 * 60 * 2:
        raise DemoIntegrityError("CONV-14 does not contain a contiguous eight-hour gap")

    all_rows = dataset.to_table(columns=["machine_id", "ts_utc_ms"]).to_pandas()
    if not all_rows.duplicated(["machine_id", "ts_utc_ms"]).any():
        raise DemoIntegrityError("no near-duplicate source rows were persisted")
    frozen = _read_machine(sensor_root, FROZEN_MACHINE)
    frozen_window = frozen[(frozen["canonical_ts_ms"] >= BASE_TS_MS + 30 * DAY_MS + 12 * 60 * 60 * 1000) & (frozen["canonical_ts_ms"] < BASE_TS_MS + 30 * DAY_MS + 18 * 60 * 60 * 1000)]
    if frozen_window["pressure_bar"].nunique(dropna=True) != 1:
        raise DemoIntegrityError("frozen pressure window is not constant")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        verify_signal(args.output_root)
    else:
        generate(args.output_root)
        verify_signal(args.output_root)


if __name__ == "__main__":
    main()
