from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as api_main


def _login(client: TestClient) -> None:
    assert client.post("/auth/login", json={"username": "planner", "password": "fixture"}).status_code == 200


def _work_order() -> dict[str, object]:
    return {
        "workorder_id": "WO-LIVE-001",
        "asset_id": "PUMP-07",
        "fault_code": "BRG-01",
        "description": "Replace drive-end bearing",
        "priority": "high",
        "status": "open",
        "created_at": "2026-08-08T08:00:00+00:00",
        "updated_at": "2026-08-08T08:00:00+00:00",
        "scheduled_start": None,
        "completed_at": None,
        "technician_notes": "Approved repair",
        "parts": ["Bearing kit"],
        "labour_hours": 2.5,
        "downtime_minutes": 90,
        "cost_gbp": 1800,
        "lead_time_days": 2,
        "order_type": "corrective",
    }


def test_erp_workspace_returns_live_history_with_inr_boundary(monkeypatch) -> None:
    monkeypatch.setattr(api_main, "_erp_read", lambda path, params=None: [_work_order()])
    monkeypatch.setattr(api_main.ERP_ACTION_AGENT, "mirror_work_orders", lambda rows: len(rows))
    client = TestClient(api_main.app)
    _login(client)

    response = client.get("/erp/workorders?limit=500")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "live"
    assert payload["total"] == 1
    assert payload["items"][0]["workorder_id"] == "WO-LIVE-001"
    assert payload["items"][0]["cost_inr"] == 229680.9
    assert "cost_gbp" not in payload["items"][0]


def test_erp_workspace_uses_historical_mirror_when_service_is_down(monkeypatch) -> None:
    def down(path, params=None):
        raise api_main._ERPReadFailure("connection refused")

    monkeypatch.setattr(api_main, "_erp_read", down)
    client = TestClient(api_main.app)
    _login(client)

    response = client.get("/erp/workorders?asset_id=PUMP-07&limit=500")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cache"
    assert payload["degraded"] is True
    assert payload["total"] >= 30
    assert all(item["asset_id"] == "PUMP-07" for item in payload["items"])
