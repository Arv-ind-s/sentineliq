import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import main as api_main
from api.auth import SEED_PASSWORD
from core.audit import AuditEvent, append_event, append_events, canonical_json, entry_hash, verify_ledger
from core.db import session_scope
from core.models import AuditLedger


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _verify_endpoint(database_url: str, monkeypatch: object) -> dict[str, object]:
    monkeypatch.setattr(api_main, "settings", SimpleNamespace(db_url=database_url))  # type: ignore[attr-defined]
    client = TestClient(api_main.app)
    login = client.post("/auth/login", json={"username": "planner", "password": SEED_PASSWORD})
    assert login.status_code == 200, login.text
    response = client.get("/audit/verify")
    assert response.status_code == 200, response.text
    return response.json()


def test_clean_chain_reports_all_entries_and_recomputes_reproducibly(tmp_path: Path, monkeypatch) -> None:
    database_url = _database_url(tmp_path / "plant.db")
    rows = append_events(
        [
            AuditEvent(actor="system", action="prediction_issued", subject="pred-1", payload={"risk": 74}, timestamp_ms=1_700_000_000_000),
            AuditEvent(actor="guardrail", action="guardrail_rejection", subject="numeric_grounding", payload={"rule": "numeric_grounding"}, timestamp_ms=1_700_000_000_001),
            AuditEvent(actor="planner", action="approval", subject="approval-1", payload={"state": "approved"}, timestamp_ms=1_700_000_000_002),
        ],
        database_url=database_url,
    )

    result = verify_ledger(database_url=database_url)

    assert result.intact is True
    assert result.entries == 3
    assert result.checked_entries == 3
    assert result.first_invalid_seq is None
    assert _verify_endpoint(database_url, monkeypatch) == {
        "intact": True,
        "entries": 3,
        "checked_entries": 3,
        "first_invalid_seq": None,
    }
    first = rows[0]
    recomputed = entry_hash(
        prev_hash=first.prev_hash,
        ts=first.ts,
        actor=first.actor,
        action=first.action,
        subject=first.subject,
        payload=first.payload_json,
    )
    assert recomputed == first.hash
    canonical_first = canonical_json(ts=first.ts, actor=first.actor, action=first.action, subject=first.subject, payload=first.payload_json, prev_hash=first.prev_hash)
    canonical_second = canonical_json(ts=first.ts, actor=first.actor, action=first.action, subject=first.subject, payload=first.payload_json, prev_hash=first.prev_hash)
    assert canonical_first == canonical_second
    assert recomputed == entry_hash(prev_hash=first.prev_hash, ts=first.ts, actor=first.actor, action=first.action, subject=first.subject, payload=first.payload_json)


def test_tampering_with_payload_reports_first_broken_sequence(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "plant.db"
    database_url = _database_url(database_path)
    append_events(
        [
            AuditEvent(actor="system", action="prediction_issued", subject="pred-1", payload={"risk": 74}, timestamp_ms=1_700_000_000_000),
            AuditEvent(actor="system", action="approval", subject="approval-1", payload={"state": "approved"}, timestamp_ms=1_700_000_000_001),
            AuditEvent(actor="system", action="erp_write", subject="pred-1", payload={"workorder_id": "WO-1"}, timestamp_ms=1_700_000_000_002),
        ],
        database_url=database_url,
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE audit_ledger SET payload_json = ? WHERE seq = 2", (json.dumps({"state": "tampered"}),))

    result = verify_ledger(database_url=database_url)

    assert result.intact is False
    assert result.entries == 3
    assert result.checked_entries == 1
    assert result.first_invalid_seq == 2
    endpoint_result = _verify_endpoint(database_url, monkeypatch)
    assert endpoint_result == {
        "intact": False,
        "entries": 3,
        "checked_entries": 1,
        "first_invalid_seq": 2,
    }


def test_append_event_keeps_required_demo_action_types_chainable(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "plant.db")
    actions = (
        "prediction_issued",
        "guardrail_rejection",
        "approval",
        "rejection",
        "erp_write",
        "blocked_unauthorised_tool_execution",
    )
    for index, action in enumerate(actions):
        append_event(actor="test", action=action, subject=f"subject-{index}", payload={"index": index}, database_url=database_url, timestamp_ms=1_700_000_000_000 + index)

    result = verify_ledger(database_url=database_url)
    with session_scope(database_url) as session:
        persisted_actions = [row.action for row in session.query(AuditLedger).order_by(AuditLedger.seq).all()]

    assert result.intact is True
    assert result.entries >= 6
    assert len(set(persisted_actions)) >= 4
    assert set(actions).issubset(persisted_actions)


def test_audit_ledger_is_readable_by_planner_and_admin_but_not_operator() -> None:
    for role in ("planner", "admin"):
        client = TestClient(api_main.app)
        login = client.post("/auth/login", json={"username": role, "password": SEED_PASSWORD})
        assert login.status_code == 200, login.text
        assert client.get("/audit").status_code == 200
        assert client.get("/audit/verify").status_code == 200

    operator = TestClient(api_main.app)
    login = operator.post("/auth/login", json={"username": "operator", "password": SEED_PASSWORD})
    assert login.status_code == 200, login.text
    assert operator.get("/audit").status_code == 403
    assert operator.get("/audit/verify").status_code == 403
