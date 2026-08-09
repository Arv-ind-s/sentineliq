from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.db import create_schema, get_engine, session_scope
from core.features import (
    FEATURE_DOCS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    FeatureCache,
    FeatureVersionMismatch,
    assert_feature_version,
    extract,
    load_manifest,
)
from core.preprocess import CHANNELS
from core.models import Machine


def _seed_machine(database_url: str) -> None:
    with session_scope(database_url) as session:
        session.add(Machine(id="M-001", name="Machine", type="test", model="X", line="L1", install_date=0, criticality=1, rated_rpm=1200, hourly_downtime_cost_gbp=1, unit_map={}))


def _window(rows: int = 360) -> pd.DataFrame:
    values = {channel: np.full(rows, 10.0, dtype=np.float64) for channel in CHANNELS}
    values["vibration_rms_mm_s"] = np.linspace(2.0, 4.0, rows)
    values["temp_bearing_c"] = np.linspace(55.0, 65.0, rows)
    values["ambient_temp_c"] = np.full(rows, 20.0)
    values["current_a"] = np.full(rows, 12.0)
    values["rpm"] = np.full(rows, 1_200.0)
    values["machine_id"] = np.full(rows, "M-001")
    values["ts_utc_ms"] = np.arange(rows, dtype=np.int64) * 60_000
    return pd.DataFrame(values)


def test_feature_contract_has_stable_documented_order() -> None:
    assert FEATURE_VERSION == "fv1"
    assert len(FEATURE_NAMES) == 41
    assert len(set(FEATURE_NAMES)) == 41
    assert set(FEATURE_NAMES) == set(FEATURE_DOCS)
    assert all(FEATURE_DOCS[name] for name in FEATURE_NAMES)


def test_extract_returns_float32_41_values_without_mutating_input() -> None:
    frame = _window()
    before = frame.copy(deep=True)

    vector = extract(frame)

    assert vector.shape == (41,)
    assert vector.dtype == np.float32
    assert np.isfinite(vector).all()
    assert vector[FEATURE_NAMES.index("temp_bearing_c_mean")] == pytest.approx(60.0)
    assert vector[FEATURE_NAMES.index("window_completeness")] == pytest.approx(1.0)
    pd.testing.assert_frame_equal(frame, before)


def test_extract_handles_missing_values_and_reports_completeness() -> None:
    frame = _window()
    frame.loc[0:9, "pressure_bar"] = np.nan

    vector = extract(frame)

    assert np.isfinite(vector).all()
    assert vector[FEATURE_NAMES.index("pressure_bar_mean")] == pytest.approx(10.0)
    assert vector[FEATURE_NAMES.index("window_completeness")] < 1.0


def test_version_mismatch_refuses_to_load(tmp_path: Path) -> None:
    manifest = {"feature_version": FEATURE_VERSION}
    assert_feature_version(manifest)
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(json.dumps({"feature_version": "fv2"}), encoding="utf-8")

    with pytest.raises(FeatureVersionMismatch, match="feature version mismatch"):
        load_manifest(manifest_path)


def test_cache_persists_and_reuses_versioned_vector(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'features.db'}"
    create_schema(database_url)
    _seed_machine(database_url)
    cache = FeatureCache(database_url)
    frame = _window()
    expected = cache.get_or_extract("M-001", 360_000, frame)

    persisted_cache = FeatureCache(database_url)
    actual = persisted_cache.get("M-001", 360_000)

    assert actual is not None
    np.testing.assert_array_equal(actual, expected)
    get_engine(database_url).dispose()


def test_memory_cache_hit_is_under_two_milliseconds(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'latency.db'}"
    create_schema(database_url)
    _seed_machine(database_url)
    cache = FeatureCache(database_url)
    vector = extract(_window())
    cache.put("M-001", 360_000, vector)

    started = time.perf_counter()
    for _ in range(100):
        assert cache.get("M-001", 360_000) is not None
    elapsed_ms = (time.perf_counter() - started) * 1000 / 100

    assert elapsed_ms < 2.0
    get_engine(database_url).dispose()
