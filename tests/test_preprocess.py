from __future__ import annotations

import time

import numpy as np
import pandas as pd

from core.preprocess import CHANNELS, preprocess_window


def _frame(timestamps: pd.Series | np.ndarray, *, machine_id: str = "M-001", **overrides: object) -> pd.DataFrame:
    size = len(timestamps)
    values: dict[str, object] = {
        "ts_utc_ms": timestamps,
        "machine_id": machine_id,
        **{channel: np.full(size, 10.0, dtype=float) for channel in CHANNELS},
    }
    values.update(overrides)
    return pd.DataFrame(values)


def test_sensor_and_erp_timestamps_share_the_same_utc_grid() -> None:
    sensor_ts = 1_735_689_600_000
    frame = pd.concat(
        [
            _frame(np.array([sensor_ts]), source=np.array(["sensor"])),
            _frame(pd.Series([pd.Timestamp("2025-01-01 00:01:30")]), source=np.array(["erp"])),
        ],
        ignore_index=True,
    )
    processed, report = preprocess_window(frame, {"source_offsets_s": {"sensor": 0, "erp": -90}, "grid_seconds": 60})

    assert report.quality == "GOOD"
    assert processed["ts_utc_ms"].tolist() == [sensor_ts]
    assert processed["ts_utc_ms"].mod(60_000).eq(0).all()


def test_fahrenheit_machine_is_harmonised_using_unit_map() -> None:
    frame = _frame(np.arange(4, dtype=np.int64) * 30_000, temp_bearing_c=np.full(4, 149.0))
    processed, _ = preprocess_window(frame, {"unit_map": {"temp_bearing_c": "F"}})

    assert 20 <= float(processed["temp_bearing_c"].mean()) <= 90


def test_hampel_replaces_spike_and_counts_it() -> None:
    timestamps = np.arange(40, dtype=np.int64) * 30_000
    vibration = np.full(40, 2.0)
    vibration[20] = 30.0
    frame = _frame(timestamps, vibration_rms_mm_s=vibration)
    processed, report = preprocess_window(frame)

    assert report.spikes_removed >= 1
    assert processed.loc[processed["ts_utc_ms"] == 600_000, "vibration_rms_mm_s"].iloc[0] < 5.0
    assert processed["despiked"].any()


def test_frozen_pressure_is_suspect() -> None:
    timestamps = np.arange(6 * 60 * 2, dtype=np.int64) * 30_000
    pressure = np.full(len(timestamps), 6.0)
    frame = _frame(timestamps, pressure_bar=pressure)
    processed, report = preprocess_window(frame)

    assert report.quality == "SUSPECT"
    assert "pressure_bar" in report.suspect_channels
    assert processed["pressure_bar"].nunique() == 1


def test_eight_hour_gap_is_insufficient_and_remains_missing() -> None:
    timestamps = np.arange(24 * 60, dtype=np.int64) * 60_000
    gap_start = 8 * 60
    gap_end = 16 * 60
    keep = np.ones(len(timestamps), dtype=bool)
    keep[gap_start:gap_end] = False
    frame = _frame(timestamps[keep])
    processed, report = preprocess_window(frame)

    assert report.quality == "INSUFFICIENT"
    assert report.completeness < 0.80
    assert processed.loc[(processed["ts_utc_ms"] >= gap_start * 60_000) & (processed["ts_utc_ms"] < gap_end * 60_000), "missing"].all()


def test_all_nan_input_returns_report_without_raising() -> None:
    timestamps = np.arange(120, dtype=np.int64) * 30_000
    frame = _frame(timestamps, **{channel: np.full(120, np.nan) for channel in CHANNELS})
    processed, report = preprocess_window(frame)

    assert len(processed) == 60
    assert report.completeness == 0.0
    assert report.quality == "INSUFFICIENT"


def test_range_clamp_prevents_extreme_values_from_propagating() -> None:
    frame = _frame(np.arange(8, dtype=np.int64) * 30_000, pressure_bar=np.array([1e308] + [6.0] * 7))
    processed, _ = preprocess_window(frame)

    assert not np.isfinite(processed["pressure_bar"]).any() or processed["pressure_bar"].max() < 100


def test_24_hour_eight_channel_window_meets_latency_budget() -> None:
    size = 2_880
    timestamps = np.arange(size, dtype=np.int64) * 30_000
    frame = _frame(timestamps, **{channel: np.full(size, 10.0) for channel in CHANNELS})
    preprocess_window(frame)  # warm pandas/NumPy rolling machinery
    started = time.perf_counter()
    preprocess_window(frame)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 25
