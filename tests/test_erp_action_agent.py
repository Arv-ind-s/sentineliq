from __future__ import annotations

import shutil
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agents.erp_action_agent import ERPActionAgent, ERPClientError, ERPTransientError
from api.auth import seed_users
from core.db import get_engine, session_scope
from core.models import Approval, AuditLedger, Machine, Prediction, WorkOrder


ROOT = Path(__file__).parents[1]


class FakeERP:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.calls: list[tuple[dict[str, object], str]] = []
        self.created: dict[str, dict[str, object]] = {}
        self._index = 0

    def create_work_order(self, payload: dict[str, object], idempotency_key: str) -> dict[str, object]:
        self.calls.append((payload, idempotency_key))
        status = self.statuses[min(self._index, len(self.statuses) - 1)]
        self._index += 1
        if status >= 500:
            raise ERPTransientError(f"ERP returned HTTP {status}", status_code=status)
        if status >= 400:
            raise ERPClientError(f"ERP returned HTTP {status}", status_code=status)
        if idempotency_key not in self.created:
            self.created[idempotency_key] = {"workorder_id": f"WO-{len(self.created) + 1}", "asset_id": payload["asset_id"]}
        return self.created[idempotency_key]


class CompleteFakeERP(FakeERP):
    def create_work_order(self, payload: dict[str, object], idempotency_key: str) -> dict[str, object]:
        result = super().create_work_order(payload, idempotency_key)
        return {
            **result,
            "fault_code": payload["fault_code"],
            "description": payload["description"],
            "technician_notes": "Approved predictive repair",
            "parts": ["Bearing kit"],
            "labour_hours": 2.5,
            "downtime_minutes": 90,
            "cost_gbp": 1800,
            "lead_time_days": 2,
            "order_type": "corrective",
            "status": "open",
            "created_at": "2026-08-08T08:00:00+00:00",
            "completed_at": None,
        }


@pytest.fixture
def database_url() -> str:
    artifact_dir = ROOT / ".test-artifacts" / f"erp-action-{uuid4().hex}"
    artifact_dir.mkdir(parents=True)
    url = f"sqlite:///{artifact_dir / 'plant.db'}"
    try:
        yield url
    finally:
        get_engine(url).dispose()
        shutil.rmtree(artifact_dir, ignore_errors=True)


def _approved_values() -> dict[str, object]:
    return {"prediction_id": "pred-pump-01-1000-model", "asset_id": "PUMP-01", "fault_code": "BRG-01", "description": "Inspect bearing", "priority": "high", "scheduled_start": None, "technician_notes": "Approved predictive repair", "parts": ["Bearing kit"], "labour_hours": 2.5, "downtime_minutes": 90, "cost_gbp": 1800.0}


def approved_database(database_url: str) -> None:
    seed_users(database_url)
    values = _approved_values()
    payload_hash = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with session_scope(database_url) as session:
        session.add(Machine(id="PUMP-01", name="Pump", type="pump", model="X", line="L1", install_date=0, criticality=1, rated_rpm=1200, hourly_downtime_cost_gbp=1800, unit_map={}))
        session.flush()
        session.add(Prediction(id=values["prediction_id"], machine_id="PUMP-01", ts=1000, p_failure=0.9, rul_p10=1, rul_p50=2, rul_p90=3, mode_probs={"bearing_wear": 1.0}, risk=90, band="CRITICAL", confidence=0.9, predictor="test", model_version="model", source="serving"))
        session.flush()
        session.add(
            Approval(
                prediction_id=str(values["prediction_id"]),
                autonomy_level="A0",
                required_role="planner",
                state="approved",
                actor="planner",
                reason="Inspect the bearing during the next planned window",
                decided_at=1,
                request_key="test-approved",
                action_payload=values,
                payload_hash=payload_hash,
            )
        )


def action_payload() -> dict[str, object]:
    values = _approved_values()
    return {**values, "approval_id": "approval-1", "payload_hash": hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def test_approval_audit_and_queue_roll_back_together(database_url, monkeypatch) -> None:
    import api.main as api_main
    import core.audit as audit

    approved_database(database_url)
    monkeypatch.setattr(api_main.settings, "db_url", database_url)
    with session_scope(database_url) as session:
        session.get(Approval, 1).state = "pending"

    def fail(*args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit, "append_in_transaction", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        api_main._transition_approval("approval-1", "approved", "planner", "repair")
    with session_scope(database_url) as session:
        assert session.get(Approval, 1).state == "pending"
        exists = session.execute(text("SELECT COUNT(*) FROM sqlite_master WHERE name='pending_erp_sync'")).scalar_one()
        assert not exists or session.execute(text("SELECT COUNT(*) FROM pending_erp_sync")).scalar_one() == 0


def test_approval_survives_restart_before_dispatch_and_replays_once(database_url, monkeypatch) -> None:
    import api.main as api_main
    from core.audit import verify_ledger
    from fastapi import HTTPException

    approved_database(database_url)
    monkeypatch.setattr(api_main.settings, "db_url", database_url)
    with session_scope(database_url) as session:
        session.get(Approval, 1).state = "pending"
    first = api_main._transition_approval("approval-1", "approved", "planner", "repair")
    repeat = api_main._transition_approval("approval-1", "approved", "planner", "other reason")
    assert first["decided_at"] == repeat["decided_at"]
    assert repeat["reason"] == "repair"
    assert first["erp_sync_status"] == "pending_sync"
    with pytest.raises(HTTPException) as conflict:
        api_main._transition_approval("approval-1", "rejected", "planner", "changed mind")
    assert conflict.value.status_code == 409
    fake = CompleteFakeERP([200])
    restarted = ERPActionAgent(database_url=database_url, erp_client=fake, start_worker=False)
    try:
        assert restarted.drain_pending() == 1
        assert restarted.drain_pending() == 0
        assert len(fake.created) == 1
        assert verify_ledger(database_url=database_url).intact
        with session_scope(database_url) as session:
            assert session.get(Approval, 1).erp_sync_status == "synced"
            assert session.execute(text("SELECT COUNT(*) FROM audit_ledger WHERE action='approval'")).scalar_one() == 1
    finally:
        restarted.close()


def test_concurrent_conflicting_decisions_have_one_winner(database_url, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    import api.main as api_main
    from core.audit import verify_ledger

    approved_database(database_url)
    monkeypatch.setattr(api_main.settings, "db_url", database_url)
    with session_scope(database_url) as session:
        session.get(Approval, 1).state = "pending"

    def decide(state):
        try:
            return api_main._transition_approval("approval-1", state, "planner", "decision")["state"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ["approved", "rejected"]))
    assert results.count(409) == 1
    assert verify_ledger(database_url=database_url).intact


def test_crash_after_remote_success_retains_idempotent_recovery(database_url, monkeypatch) -> None:
    approved_database(database_url)
    fake = CompleteFakeERP([200])
    agent = ERPActionAgent(database_url=database_url, erp_client=fake, start_worker=False, pending_retry_delay_s=0)

    def crash(result):
        raise RuntimeError("local mirror unavailable")

    monkeypatch.setattr(agent, "_mirror_work_order", crash)
    with pytest.raises(RuntimeError, match="local mirror unavailable"):
        agent.create_work_order(action_payload(), actor="planner", role="planner")
    agent.close()
    restarted = ERPActionAgent(database_url=database_url, erp_client=fake, start_worker=False)
    try:
        assert restarted.drain_pending() == 1
        assert len(fake.created) == 1
    finally:
        restarted.close()


def test_missing_approval_raises_permission_error_and_writes_security_event(database_url: str, caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeERP([200])
    agent = ERPActionAgent(database_url=database_url, erp_client=fake, start_worker=False)
    caplog.set_level("WARNING")

    with pytest.raises(PermissionError):
        agent.create_work_order(action_payload(), actor="planner", role="planner")
    agent.close()

    assert fake.calls == []
    assert "blocked unauthorised ERP tool execution" in caplog.text
    with session_scope(database_url) as session:
        event = session.query(AuditLedger).filter_by(action="blocked_unauthorised_tool_execution").one()
        assert event.subject == "pred-pump-01-1000-model"


def test_approval_replay_uses_one_idempotency_key_and_one_erp_work_order(database_url: str) -> None:
    approved_database(database_url)
    fake = FakeERP([200, 200])
    agent = ERPActionAgent(database_url=database_url, erp_client=fake, start_worker=False)

    first = agent.create_work_order(action_payload(), actor="planner", role="planner")
    second = agent.create_work_order(action_payload(), actor="planner", role="planner")
    agent.close()

    assert first["workorder_id"] == second["workorder_id"]
    assert len(fake.created) == 1
    assert len(fake.calls) == 2
    assert fake.calls[0][1] == fake.calls[1][1]


def test_successful_write_is_upserted_into_the_offline_history_mirror(database_url: str) -> None:
    approved_database(database_url)
    agent = ERPActionAgent(database_url=database_url, erp_client=CompleteFakeERP([200]), start_worker=False)

    result = agent.create_work_order(action_payload(), actor="planner", role="planner")
    agent.close()

    with session_scope(database_url) as session:
        mirrored = session.get(WorkOrder, result["workorder_id"])
        assert mirrored is not None
        assert mirrored.machine_id == "PUMP-01"
        assert mirrored.fault_code == "BRG-01"
        assert mirrored.cost_gbp == 1800
        assert mirrored.parts_used == [{"name": "Bearing kit"}]


def test_four_x_is_not_retried(database_url: str) -> None:
    approved_database(database_url)
    fake = FakeERP([400, 200])
    agent = ERPActionAgent(database_url=database_url, erp_client=fake, start_worker=False, retry_delays_s=(0, 0), jitter_s=0)

    with pytest.raises(ERPClientError):
        agent.create_work_order(action_payload(), actor="planner", role="planner")
    agent.close()

    assert len(fake.calls) == 1


def test_transient_failure_is_retried_three_times_then_drained(database_url: str) -> None:
    approved_database(database_url)
    fake = FakeERP([503, 503, 503, 200])
    agent = ERPActionAgent(
        database_url=database_url,
        erp_client=fake,
        start_worker=False,
        retry_delays_s=(0, 0),
        jitter_s=0,
        pending_retry_delay_s=0,
    )

    queued = agent.create_work_order(action_payload(), actor="planner", role="planner")
    assert queued["status"] == "pending_sync"
    assert len(fake.calls) == 3
    with session_scope(database_url) as session:
        assert session.execute(text("SELECT COUNT(*) FROM pending_erp_sync")).scalar_one() == 1
        approval = session.get(Approval, 1)
        assert approval is not None and approval.erp_sync_status == "pending_sync"

    assert agent.drain_pending() == 1
    agent.close()
    assert len(fake.calls) == 4
    with session_scope(database_url) as session:
        assert session.execute(text("SELECT COUNT(*) FROM pending_erp_sync")).scalar_one() == 0
        approval = session.get(Approval, 1)
        assert approval is not None
        assert approval.erp_sync_status == "synced"
        assert approval.erp_workorder_id == "WO-1"


def test_approval_route_writes_a_work_order_visible_from_the_erp_api(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.main as api_main
    from erp.app import app as erp_app

    erp_dir = ROOT / ".test-artifacts" / f"erp-route-{uuid4().hex}"
    erp_dir.mkdir(parents=True)
    plant_path = erp_dir / "plant.db"
    shutil.copy2(ROOT / "data" / "plant.db", plant_path)
    database_url = f"sqlite:///{plant_path}"
    monkeypatch.setenv("ERP_API_KEY", "test-erp-key")
    monkeypatch.setenv("ERP_DB_PATH", str(erp_dir / "erp.db"))
    monkeypatch.setattr(api_main.settings, "db_url", database_url)
    erp_service = TestClient(erp_app)

    class ServiceAdapter:
        def create_work_order(self, payload: dict[str, object], idempotency_key: str) -> dict[str, object]:
            response = erp_service.post(
                "/erp/v1/workorders",
                headers={
                    "Authorization": "Bearer test-erp-key",
                    "X-ERP-Scopes": "workorder:write",
                    "Idempotency-Key": idempotency_key,
                },
                json=payload,
            )
            if response.status_code >= 500:
                raise ERPTransientError(response.text, status_code=response.status_code)
            if response.status_code >= 400:
                raise ERPClientError(response.text, status_code=response.status_code)
            return response.json()

    agent = ERPActionAgent(database_url=database_url, erp_client=ServiceAdapter(), start_worker=False)
    monkeypatch.setattr(api_main, "ERP_ACTION_AGENT", agent)
    try:
        values = _approved_values()
        payload_hash = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with session_scope(database_url) as session:
            session.merge(Prediction(id=values["prediction_id"], machine_id="PUMP-01", ts=1000, p_failure=0.9, rul_p10=1, rul_p50=2, rul_p90=3, mode_probs={"bearing_wear": 1.0}, risk=90, band="CRITICAL", confidence=0.9, predictor="test", model_version="model", source="serving"))
            session.flush()
            approval = Approval(prediction_id=str(values["prediction_id"]), autonomy_level="A0", required_role="planner", state="pending", request_key=f"route-{uuid4().hex}", action_payload=values, payload_hash=payload_hash)
            session.add(approval)
            session.flush()
            approval_id = f"approval-{approval.id}"
        api_client = TestClient(api_main.app)
        assert api_client.post("/auth/login", json={"username": "planner", "password": "fixture"}).status_code == 200
        response = api_client.post(f"/approvals/{approval_id}/approve", json={"reason": "Approved for the next planned window"})
        assert response.status_code == 200, response.text

        workorders = erp_service.get("/erp/v1/workorders", headers={"Authorization": "Bearer test-erp-key", "X-ERP-Scopes": "workorder:read"})
        assert workorders.status_code == 200
        assert any("Approved predictive repair" in row["technician_notes"] for row in workorders.json())
    finally:
        agent.close()
        get_engine(database_url).dispose()
        shutil.rmtree(erp_dir, ignore_errors=True)
