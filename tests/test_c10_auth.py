from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app


def test_login_cookie_is_sent_to_session_endpoint_and_exposes_role_only() -> None:
    client = TestClient(app)

    login = client.post("/auth/login", json={"username": "planner", "password": "fixture"})
    assert login.status_code == 200
    assert "sentinel_session=" in login.headers["set-cookie"]

    session = client.get("/auth/me")
    assert session.status_code == 200
    assert session.json() == {"username": "planner", "role": "planner", "can_switch_persona": False}
    assert "token" not in session.text.lower()
    assert "jwt" not in session.text.lower()


def test_wrong_password_uses_plain_generic_error_without_traceback() -> None:
    response = TestClient(app).post("/auth/login", json={"username": "planner", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password"
    assert "Traceback" not in response.text
    assert "planner" not in response.json()["error"]["message"]


def test_logout_clears_cookie_and_invalidates_session() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "planner", "password": "fixture"}).status_code == 200

    logout = client.post("/auth/logout")
    assert logout.status_code == 200
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert client.get("/auth/me").status_code == 401


def test_operator_cannot_approve_even_if_the_ui_is_bypassed() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "operator", "password": "fixture"}).status_code == 200

    response = client.post("/approvals/c10-security-check/approve", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_admin_can_switch_personas_without_granting_admin_actions_to_the_selected_role() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    switched = client.post("/auth/persona", json={"role": "operator"})

    assert switched.status_code == 200
    assert switched.json() == {"username": "admin", "role": "operator", "can_switch_persona": True}
    assert client.get("/auth/me").json() == switched.json()
    assert client.post("/approvals/c10-persona-switch/approve", json={}).status_code == 403

    restored = client.post("/auth/persona", json={"role": "admin"})
    assert restored.status_code == 200
    assert restored.json()["role"] == "admin"


def test_non_admin_cannot_switch_persona() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "planner", "password": "fixture"}).status_code == 200

    response = client.post("/auth/persona", json={"role": "operator"})

    assert response.status_code == 403
    assert client.get("/auth/me").json()["role"] == "planner"


def test_ui_does_not_contain_cross_origin_api_urls() -> None:
    ui_root = Path(__file__).resolve().parents[1] / "ui" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in ui_root.rglob("*.*") if path.is_file())

    assert "127.0.0.1" not in source
    assert "localhost:8000" not in source
