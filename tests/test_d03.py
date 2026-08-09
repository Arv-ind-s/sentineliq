from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.config import settings
from core.ingest import read_ingest_points
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def enabled_rehearsal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the retained backend in isolation without enabling it by default."""
    monkeypatch.setattr(settings.ui.features, "rehearsal", True)


def authenticated_client(role: str = "technician") -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": role, "password": "fixture"})
    assert response.status_code == 200, response.text
    return client


def reset_rehearsal() -> None:
    client = authenticated_client("technician")
    response = client.post("/rehearsal/reset")
    assert response.status_code == 200, response.text


def test_bearing_injection_uses_ingest_path_and_raises_risk(enabled_rehearsal: None) -> None:
    client = authenticated_client()
    before = {item["id"]: item["risk"] for item in client.get("/machines").json()}
    onset = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)

    response = client.post(
        "/rehearsal/inject",
        json={"machine_id": "PUMP-07", "mode": "bearing_wear", "severity": "high", "onset": onset.isoformat()},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "injected"
    after = {item["id"]: item["risk"] for item in client.get("/machines").json()}
    assert after["PUMP-07"] > before["PUMP-07"]
    points = read_ingest_points("PUMP-07", int(onset.timestamp() * 1000) - 1, int(onset.timestamp() * 1000) + 1)
    assert any(point.is_simulated for point in points)
    alerts = client.get("/alerts", params={"state": "open"}).json()
    assert any(item["machine_id"] == "PUMP-07" and item["kind"] == "machine" for item in alerts)
    reset_rehearsal()


def test_reset_restores_every_original_risk_score(enabled_rehearsal: None) -> None:
    client = authenticated_client()
    before = {item["id"]: item["risk"] for item in client.get("/machines").json()}
    response = client.post(
        "/rehearsal/inject",
        json={"machine_id": "PRESS-03", "mode": "motor_overheat", "severity": "critical", "onset": "2030-01-02T12:00:00Z"},
    )
    assert response.status_code == 200, response.text

    reset = client.post("/rehearsal/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json()["restored_machines"] == 15
    after = {item["id"]: item["risk"] for item in client.get("/machines").json()}
    assert after == before


def test_scoreboard_reports_detection_lead_time_and_drift_decline(enabled_rehearsal: None) -> None:
    client = authenticated_client()
    for offset, mode in enumerate(("bearing_wear", "sensor_drift")):
        response = client.post(
            "/rehearsal/inject",
            json={"machine_id": "PUMP-01", "mode": mode, "severity": "high", "onset": f"2030-02-0{offset + 1}T12:00:00Z"},
        )
        assert response.status_code == 200, response.text

    scoreboard = client.get("/rehearsal/scoreboard")
    assert scoreboard.status_code == 200, scoreboard.text
    payload = scoreboard.json()
    assert {"detection_rate", "lead_time_hours", "mode_accuracy", "scenarios", "all_passed"} <= set(payload)
    assert payload["detection_rate"] == 1.0
    assert payload["mode_accuracy"] == 1.0
    assert payload["lead_time_hours"] == 24.0
    drift = next(item for item in payload["scenarios"] if item["scenario"] == "sensor_drift")
    assert drift["passed"] is True
    assert drift["observed"] == "declined_mechanical_fault"
    reset_rehearsal()


def test_operator_cannot_inject_rehearsal_fault(enabled_rehearsal: None) -> None:
    client = authenticated_client("operator")
    response = client.post(
        "/rehearsal/inject",
        json={"machine_id": "PUMP-01", "mode": "bearing_wear", "severity": "high", "onset": "2030-03-01T12:00:00Z"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_core_pipeline_has_no_simulation_branch() -> None:
    core_sources = "\n".join((ROOT / "core" / "ingest.py").read_text(encoding="utf-8").splitlines())
    assert "if is_simulated" not in core_sources
    assert "is_simulated=true" not in core_sources
