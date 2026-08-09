from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from core.config import settings

ROOT = Path(__file__).resolve().parents[1]


def authenticated_client() -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": "admin", "password": "fixture"})
    assert response.status_code == 200, response.text
    return client


def test_rehearsal_console_is_cut_in_the_default_configuration() -> None:
    assert settings.ui.features.model_dump() == {"digital_twin": True, "rehearsal": False, "ledger": True}


def test_cut_rehearsal_routes_return_not_found() -> None:
    client = authenticated_client()
    requests = [
        ("POST", "/rehearsal/inject", {"machine_id": "PUMP-01", "mode": "bearing_wear", "severity": "high", "onset": "2026-01-01T12:00:00Z"}),
        ("POST", "/rehearsal/reset", None),
        ("GET", "/rehearsal/scoreboard", None),
    ]

    for method, path, body in requests:
        response = client.request(method, path, json=body)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["error"]["code"] == "feature_disabled"


def test_cut_rehearsal_has_no_frontend_route_or_product_claim() -> None:
    app_source = (ROOT / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")
    readme_source = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "/rehearsal" not in app_source
    assert "Rehearsal Console" not in app_source
    assert "rehearsal controls" not in readme_source


def test_core_machine_flow_remains_available_after_cut() -> None:
    client = authenticated_client()
    assert client.get("/health").status_code == 200
    assert client.get("/machines").status_code == 200
    assert client.get("/machines/PUMP-07").status_code == 200
    assert client.get("/ledger").status_code == 200
