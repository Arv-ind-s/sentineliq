from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from agents.base import RunContext
from agents.sensor_agent import SensorAgent
from core.db import create_schema, session_scope
from core.models import Alert, Evidence, Machine, Prediction
from core.preprocess import CHANNELS, QualityReport


def _window(rows: int = 12) -> pd.DataFrame:
    timestamps = 1_700_000_000_000 + np.arange(rows, dtype=np.int64) * 60_000
    values = {
        "machine_id": ["PUMP-07"] * rows,
        "ts_utc_ms": timestamps,
    }
    for index, channel in enumerate(CHANNELS):
        values[channel] = np.full(rows, 10.0 + index, dtype=float) + np.arange(rows) * 0.01
    return pd.DataFrame(values)


class FakeTSDB:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def read_window(self, machine_id: str, t0: object, t1: object, channels: tuple[str, ...]) -> pd.DataFrame:
        self.calls += 1
        assert machine_id == "PUMP-07"
        assert channels == CHANNELS
        return self.frame.copy()


class FakePredictor:
    def __init__(self) -> None:
        self.calls = 0

    def score(self, features: np.ndarray, machine_meta: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        assert features.shape == (41,)
        return {
            "p_failure_24h": 0.87,
            "rul_p10": 2.0,
            "rul_p50": 4.2,
            "rul_p90": 7.0,
            "mode_probs": {"bearing_wear": 0.9},
            "top_features": [{"name": "vibration_rms_mm_s", "value": 10.2}],
            "predictor": "fake",
            "model_version": "fv1-test",
        }


def _seed_machine(database_url: str) -> None:
    create_schema(database_url)
    with session_scope(database_url) as session:
        session.add(Machine(id="PUMP-07", name="Pump", type="pump", model="X", line="L1", install_date=0, criticality=1, rated_rpm=1200, hourly_downtime_cost_gbp=1, unit_map={}))


def test_sensor_agent_is_deterministic_and_persists_concrete_evidence(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    _seed_machine(database_url)
    predictor = FakePredictor()
    tsdb = FakeTSDB(_window())
    agent = SensorAgent(tsdb, predictor, database_url=database_url, read_retry_delays_s=(0, 0, 0))

    result = agent.run(
        RunContext(
            trace_id="trace-sensor-good",
            payload={"machine_id": "PUMP-07", "t0": 1_700_000_000_000, "t1": 1_700_000_700_000},
        )
    )

    assert result.ok is True
    assert result.output["refused"] is False
    assert 3 <= len(result.evidence) <= 5
    assert all({"id", "channel", "t_start", "t_end", "stat", "value"} <= set(item) for item in result.evidence)
    assert predictor.calls == 1
    assert tsdb.calls == 1
    with session_scope(database_url) as session:
        assert session.query(Prediction).count() == 1
        rows = session.query(Evidence).all()
        assert len(rows) == len(result.evidence)
        assert all(row.payload["channel"] for row in rows)


def test_insufficient_quality_refuses_prediction_and_alerts_maintenance_owner(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    _seed_machine(database_url)
    report = QualityReport(completeness=0.62, missing_pct=0.38, quality="INSUFFICIENT")

    def preprocess(frame: pd.DataFrame, _: object) -> tuple[pd.DataFrame, QualityReport]:
        return frame, report

    predictor = FakePredictor()
    agent = SensorAgent(
        FakeTSDB(_window()),
        predictor,
        database_url=database_url,
        preprocess_fn=preprocess,
        read_retry_delays_s=(0, 0, 0),
    )
    result = agent.run(RunContext(payload={"machine_id": "PUMP-07", "t0": 1_700_000_000_000, "t1": 1_700_000_700_000}))

    assert result.ok is True
    assert result.output["refused"] is True
    assert result.output["alerts"][0]["route"] == "maintenance-systems"
    assert predictor.calls == 0
    with session_scope(database_url) as session:
        assert session.query(Prediction).count() == 0
        assert session.query(Alert).one().kind == "data_integrity"


def test_transient_sensor_read_failure_returns_sensor_unavailable_after_three_attempts(tmp_path: Path) -> None:
    class FailingTSDB:
        calls = 0

        def read_window(self, *_: object) -> pd.DataFrame:
            self.calls += 1
            raise RuntimeError("DuckDB unavailable")

    tsdb = FailingTSDB()
    agent = SensorAgent(tsdb, FakePredictor(), database_url=f"sqlite:///{tmp_path / 'plant.db'}", read_retry_delays_s=(0, 0, 0))
    result = agent.run(RunContext(payload={"machine_id": "PUMP-07", "t0": 1_700_000_000_000, "t1": 1_700_000_700_000}))

    assert result.ok is False
    assert result.output["reason"] == "sensor_unavailable"
    assert result.retries == 2
    assert tsdb.calls == 3


def test_warm_sensor_window_completes_within_latency_budget(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    _seed_machine(database_url)
    context = RunContext(payload={"machine_id": "PUMP-07", "t0": 1_700_000_000_000, "t1": 1_700_000_700_000})
    agent = SensorAgent(
        FakeTSDB(_window()),
        FakePredictor(),
        database_url=database_url,
        read_retry_delays_s=(0, 0, 0),
    )
    agent.run(context)

    result = agent.run(context)

    assert result.ok is True
    assert result.latency_ms < 400
