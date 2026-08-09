from __future__ import annotations

from sqlalchemy import select

from agents.sensor_agent import SensorEvidence, _persist_prediction_and_evidence
from core.db import create_schema, session_scope
from core.models import Evidence, Machine


def _prediction(value: float) -> dict[str, object]:
    return {
        "p_failure_24h": value,
        "rul_p10": 1.0,
        "rul_p50": 2.0,
        "rul_p90": 3.0,
        "mode_probs": {"bearing_wear": 1.0},
        "confidence": 0.9,
        "predictor": "test",
        "model_version": "test-model",
    }


def test_local_s1_citations_are_retained_for_each_prediction(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    create_schema(database_url)
    with session_scope(database_url) as session:
        session.add(Machine(id="PUMP-01", name="Pump", type="pump", model="X", line="L1", install_date=0, criticality=1, rated_rpm=1200, hourly_downtime_cost_gbp=1, unit_map={}))

    first = SensorEvidence("S1", "sensor", "rpm", 1, 2, "mean", 1.1)
    second = SensorEvidence("S1", "sensor", "rpm", 3, 4, "mean", 9.9)
    _persist_prediction_and_evidence(database_url=database_url, prediction_id="pred-a", machine_id="PUMP-01", ts=2, prediction=_prediction(.2), evidence=[first])
    _persist_prediction_and_evidence(database_url=database_url, prediction_id="pred-b", machine_id="PUMP-01", ts=4, prediction=_prediction(.8), evidence=[second])

    with session_scope(database_url) as session:
        rows = session.scalars(select(Evidence).order_by(Evidence.prediction_id)).all()
    assert [(row.prediction_id, row.citation_id, row.payload["value"]) for row in rows] == [
        ("pred-a", "S1", 1.1),
        ("pred-b", "S1", 9.9),
    ]
