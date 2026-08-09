from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pytest
from sqlalchemy import select

from core.db import get_engine, session_scope
from core.models import FaultEvent, Machine
from data.generate import (
    BASE_TS_MS,
    CHANNELS,
    DAY_MS,
    FAHRENHEIT_MACHINES,
    FROZEN_MACHINE,
    generate,
    verify_signal,
)


@pytest.fixture(scope="module")
def generated_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output_root = tmp_path_factory.mktemp("a03")
    generate(output_root)
    verify_signal(output_root)
    yield output_root
    get_engine(f"sqlite:///{output_root / 'plant.db'}").dispose()


def _parquet_checksums(sensor_root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in sorted(sensor_root.rglob("*.parquet")):
        checksums[str(path.relative_to(sensor_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return checksums


def test_two_runs_are_byte_identical(generated_data: Path, tmp_path: Path) -> None:
    second_root = tmp_path / "second"
    generate(second_root)

    assert _parquet_checksums(generated_data / "sensors") == _parquet_checksums(second_root / "sensors")


def test_inventory_and_sample_partition(generated_data: Path) -> None:
    sensor_root = generated_data / "sensors"
    assert (sensor_root / "machine_id=PUMP-01").exists()
    assert list((sensor_root / "machine_id=PUMP-01").glob("date=*.parquet"))

    with session_scope(f"sqlite:///{generated_data / 'plant.db'}") as session:
        machines = list(session.scalars(select(Machine)))
        events = list(session.scalars(select(FaultEvent)))
    assert len(machines) == 15
    assert len({machine.line for machine in machines}) == 3
    assert len(events) == 60
    counts = {mode: sum(event.mode == mode for event in events) for mode in {event.mode for event in events}}
    assert all(counts.get(mode, 0) >= 10 for mode in {
        "bearing_wear": 10,
        "rotor_imbalance": 10,
        "seal_leak": 10,
        "motor_overheat": 10,
        "lubrication_degradation": 10,
    })

    drift = pd.read_parquet(generated_data / "drift_intervals.parquet")
    assert len(drift) == 12
    assert drift["failure_event_id"].isna().all()


def test_all_six_defects_are_detectable(generated_data: Path) -> None:
    sensor_root = generated_data / "sensors"
    dataset = ds.dataset(sensor_root, format="parquet", partitioning="hive")

    offsets = set(dataset.to_table(columns=["clock_offset_s"]).column("clock_offset_s").to_pylist())
    assert {-90, 90}.issubset(offsets)

    dropout_rows = spike_rows = total_rows = 0
    for batch in dataset.scanner(columns=list(CHANNELS)).to_batches():
        frame = batch.to_pandas()
        dropout_rows += int(frame[list(CHANNELS)].isna().all(axis=1).sum())
        spike_rows += int((frame["vibration_rms_mm_s"] > 20.0).sum())
        total_rows += len(frame)
    assert 0.010 <= dropout_rows / total_rows <= 0.012
    assert 0.003 <= spike_rows / total_rows <= 0.005

    keys = dataset.to_table(columns=["machine_id", "ts_utc_ms"]).to_pandas()
    assert keys.duplicated(["machine_id", "ts_utc_ms"]).any()

    with session_scope(f"sqlite:///{generated_data / 'plant.db'}") as session:
        machines = {machine.id: machine for machine in session.scalars(select(Machine))}
    assert {machine_id for machine_id, machine in machines.items() if machine.unit_map["temp_bearing_c"] == "F"} == FAHRENHEIT_MACHINES
    for machine_id in FAHRENHEIT_MACHINES:
        values = dataset.to_table(filter=ds.field("machine_id") == machine_id, columns=["temp_bearing_c"]).column(0).to_numpy(zero_copy_only=False)
        assert float(np.nanmedian(values)) > 100.0

    frozen = dataset.to_table(filter=ds.field("machine_id") == FROZEN_MACHINE).to_pandas()
    window = frozen[(frozen["canonical_ts_ms"] >= BASE_TS_MS + 30 * DAY_MS + 12 * 60 * 60 * 1000) & (frozen["canonical_ts_ms"] < BASE_TS_MS + 30 * DAY_MS + 18 * 60 * 60 * 1000)]
    assert window["pressure_bar"].nunique(dropna=True) == 1


def test_verify_signal_is_model_free_and_demo_notes_cover_scenarios(generated_data: Path) -> None:
    verify_signal(generated_data)
    notes = (generated_data / "DEMO_NOTES.md").read_text(encoding="utf-8")
    for scenario in ("PUMP-07", "PRESS-03", "CNC-11", "CONV-14"):
        assert scenario in notes
    assert "measured alert crossing" in notes
    assert "tau" not in notes.lower()
