from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import api.main as api_main
from fastapi.testclient import TestClient

from api.schemas import DiagnosisResponse, RecommendedAction
from core.db import dispose_engine, session_scope
from core.models import Prediction


ROOT = Path(__file__).resolve().parents[1]


def _diagnosis(prediction_id: str) -> DiagnosisResponse:
    return DiagnosisResponse(
        diagnosis_id="diag-routing-test", machine_id="PUMP-01", prediction_id=prediction_id,
        fault_mode="bearing_wear", explanation="Bearing wear evidence requires review.", confidence=0.9,
        reasoning=["Current telemetry supports review. [S1]"], facts=["Risk is elevated. [S1]"], assumptions=[],
        limits=["Inspection is required."], actions=["Inspect bearing"],
        recommended_actions=[RecommendedAction(action="Inspect bearing", priority="high", rationale="Review telemetry. [S1]", parts=["Bearing kit"], est_labour_hours=2.0, window="Next window", citations=["S1"])],
        risk=80, risk_terms={}, citations=[], generated=False, offline_mode=True, model="test", provider="local",
        prompt_version="v1", generated_at=datetime.now(UTC),
    )


def test_orchestrator_routing_creates_one_idempotent_pending_approval(monkeypatch, tmp_path: Path) -> None:
    plant_path = tmp_path / "plant.db"
    shutil.copy2(ROOT / "data" / "plant.db", plant_path)
    database_url = f"sqlite:///{plant_path.as_posix()}"
    monkeypatch.setattr(api_main.settings, "db_url", database_url)
    prediction_id = "pred-agent-routing-test"
    route = {
        "autonomy": "A0",
        "action": "planner_approval",
        "required_role": "planner",
        "writes_blocked": True,
    }

    try:
        with session_scope(database_url) as session:
            session.add(Prediction(id=prediction_id, machine_id="PUMP-01", ts=123, p_failure=.8, rul_p10=1, rul_p50=2, rul_p90=3, mode_probs={"bearing_wear": 1.0}, risk=80, band="CRITICAL", confidence=.9, predictor="test", model_version="test", source="serving"))
        diagnosis = _diagnosis(prediction_id)
        first = api_main._ensure_routed_approval(diagnosis, route)
        second = api_main._ensure_routed_approval(diagnosis, route)

        assert first == second
        with sqlite3.connect(plant_path) as connection:
            row = connection.execute(
                """SELECT COUNT(*), autonomy_level, required_role, state
                   FROM approvals WHERE prediction_id = ?""",
                (prediction_id,),
            ).fetchone()
        assert row == (1, "A0", "planner", "pending")
    finally:
        dispose_engine(database_url)


def test_a1_technician_acknowledges_without_erp_write(monkeypatch, tmp_path: Path) -> None:
    plant_path = tmp_path / "plant.db"
    shutil.copy2(ROOT / "data" / "plant.db", plant_path)
    database_url = f"sqlite:///{plant_path.as_posix()}"
    monkeypatch.setattr(api_main.settings, "db_url", database_url)
    prediction_id = "pred-a1-routing-test"
    with session_scope(database_url) as session:
        session.add(Prediction(id=prediction_id, machine_id="PUMP-01", ts=124, p_failure=.5, rul_p10=1, rul_p50=2, rul_p90=3, mode_probs={"bearing_wear": 1.0}, risk=50, band="MEDIUM", confidence=.8, predictor="test", model_version="test", source="serving"))
    approval_id = api_main._ensure_routed_approval(_diagnosis(prediction_id), {"autonomy": "A1", "action": "technician_ack", "required_role": "technician"})
    assert approval_id is not None

    class NoERP:
        def create_work_order(self, *_args, **_kwargs):
            raise AssertionError("A1 acknowledgement must not invoke ERP")

    monkeypatch.setattr(api_main, "ERP_ACTION_AGENT", NoERP())
    client = TestClient(api_main.app)
    assert client.post("/auth/login", json={"username": "technician", "password": "fixture"}).status_code == 200
    response = client.post(f"/approvals/approval-{approval_id}/acknowledge", json={})
    assert response.status_code == 200
    assert response.json()["state"] == "acknowledged"
    assert response.json()["erp_sync_status"] == "not_requested"
    dispose_engine(database_url)
