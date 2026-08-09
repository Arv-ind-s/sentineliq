from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from api.schemas import MachineSummary


client = TestClient(app)
assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200


def test_openapi_lists_all_appendix_b_routes() -> None:
    routes = set(client.get("/openapi.json").json()["paths"])

    expected = {
        "/auth/login", "/auth/logout", "/health", "/metrics", "/machines", "/machines/{machine_id}",
        "/machines/{machine_id}/telemetry", "/machines/{machine_id}/prediction", "/machines/{machine_id}/diagnose",
        "/diagnoses/{diagnosis_id}", "/runs/{trace_id}", "/alerts", "/alerts/{alert_id}", "/approvals",
        "/approvals/{approval_id}/approve", "/approvals/{approval_id}/acknowledge", "/approvals/{approval_id}/reject", "/schedule", "/ingest/batch",
        "/ingest/dlq", "/ledger", "/eval/report", "/audit", "/audit/verify", "/rehearsal/inject",
        "/rehearsal/reset", "/rehearsal/scoreboard", "/stream/events", "/stream/speed", "/stream/pause", "/stream/reset", "/meta",
    }

    assert expected <= routes
    assert len(routes) >= 25


def test_machines_returns_fifteen_contract_validated_fixtures() -> None:
    response = client.get("/machines")

    assert response.status_code == 200
    machines = [MachineSummary.model_validate(item) for item in response.json()]
    assert len(machines) == 15


def test_machine_detail_exposes_ground_truth_fault_events() -> None:
    response = client.get("/machines/PUMP-07")

    assert response.status_code == 200
    detail = response.json()
    assert [component["name"] for component in detail["components"]] == ["bearing", "shaft", "seal", "motor housing", "impeller"]
    assert all(component["channel"] for component in detail["components"])
    assert all(0 <= component["risk"] <= 100 for component in detail["components"])
    assert sum(component["predicted"] for component in detail["components"]) == 1
    assert detail["components"][0]["predicted"] is True
    assert detail["fault_events"]
    event = detail["fault_events"][-1]
    assert event["machine_id"] == "PUMP-07"
    assert event["mode"] == "bearing_wear"
    assert event["onset_ts"]
    assert detail["history"]
    assert detail["hourly_downtime_cost_inr"] > 0
    assert "hourly_downtime_cost_gbp" not in detail


def test_conv14_telemetry_exposes_distinct_quality_segments() -> None:
    response = client.get(
        "/machines/CONV-14/telemetry",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z", "interval": 60, "channels": "vibration_rms_mm_s,vibration_kurtosis,temp_bearing_c,temp_motor_c,pressure_bar,current_a,rpm,ambient_temp_c"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["channels"]) == 8
    assert {flag for point in payload["series"] for flag in point["quality_flags"]} >= {"interpolated", "missing", "despiked"}


def test_all_json_fixture_routes_validate_against_declared_response_models() -> None:
    requests = [
        ("/health", "GET", None), ("/metrics", "GET", None), ("/machines/PUMP-01", "GET", None),
        ("/machines/PUMP-01/telemetry", "GET", None), ("/machines/PUMP-01/prediction", "GET", None),
        ("/machines/PUMP-01/diagnose", "POST", {"force_refresh": False}), ("/diagnoses/demo", "GET", None, 404),
        ("/runs/demo", "GET", None, 404), ("/alerts", "GET", None), ("/alerts/definitely-unknown-contract-id", "PATCH", {"state": "acknowledged"}, 404),
        ("/approvals", "GET", None), ("/approvals/demo/approve", "POST", {}, 404), ("/approvals/demo/acknowledge", "POST", {}, 404), ("/approvals/demo/reject", "POST", {"reason": "Fixture test"}, 404),
        ("/schedule", "GET", None), ("/stream/speed?speed=600", "POST", None), ("/stream/pause?paused=false", "POST", None), ("/ingest/batch", "POST", {"points": [{"machine_id": "PUMP-01", "ts": "2026-01-01T12:00:00Z", "channels": {"rpm": 1200}}]}),
        ("/ingest/dlq", "GET", None), ("/ledger", "GET", None), ("/eval/report", "GET", None),
        ("/audit", "GET", None, 200), ("/audit/verify", "GET", None, 200),
        ("/rehearsal/inject", "POST", {"machine_id": "PUMP-01", "mode": "bearing_wear", "severity": "high", "onset": "2026-01-01T12:00:00Z"}, 404),
        ("/rehearsal/reset", "POST", None, 404), ("/rehearsal/scoreboard", "GET", None, 404),
        ("/stream/reset", "POST", None, 200), ("/meta", "GET", None, 200),
    ]

    for request in requests:
        path, method, body = request[:3]
        expected_status = request[3] if len(request) == 4 else 200
        response = client.request(method, path, json=body)
        assert response.status_code == expected_status, (method, path, response.text)


def test_error_responses_use_the_frozen_envelope() -> None:
    response = client.get("/machines/UNKNOWN")

    assert response.status_code == 404
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message", "trace_id"}


def test_request_models_forbid_unknown_fields() -> None:
    response = client.post("/auth/login", json={"username": "planner", "password": "x", "unexpected": True})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_erp_write_contract_accepts_only_an_approval_id() -> None:
    response = client.post(
        "/erp/workorders",
        json={"approval_id": "approval-1", "asset_id": "PUMP-01", "description": "client override"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_login_freezes_same_site_http_only_cookie() -> None:
    response = client.post("/auth/login", json={"username": "planner", "password": "fixture"})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "sentinel_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_rejection_reason_is_required_by_the_contract() -> None:
    response = client.post("/approvals/demo/reject", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
