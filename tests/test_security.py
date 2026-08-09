from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.auth import COOKIE_NAME, SEED_PASSWORD, create_access_token, seed_users, verify_token
from api.main import app
from core.db import session_scope
from core.models import User


ROLES = ("operator", "technician", "planner", "admin")


def authenticated_client(role: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": role, "password": SEED_PASSWORD})
    assert response.status_code == 200, response.text
    return client


def test_mutating_routes_reject_every_insufficient_role() -> None:
    cases = [
        ("/machines/PUMP-01/diagnose", "POST", {"force_refresh": False}, {"technician", "planner", "admin"}),
        ("/alerts/security-test", "PATCH", {"state": "acknowledged"}, {"technician", "planner", "admin"}),
        ("/approvals/security-test/approve", "POST", {}, {"planner", "admin"}),
        ("/approvals/security-test/reject", "POST", {"reason": "security test"}, {"technician", "planner", "admin"}),
        ("/ingest/batch", "POST", {"points": [{"machine_id": "PUMP-01", "ts": "2026-01-01T12:00:00Z", "channels": {"rpm": 1200}}]}, {"admin"}),
        ("/rehearsal/inject", "POST", {"machine_id": "PUMP-01", "mode": "bearing_wear", "severity": "high", "onset": "2026-01-01T12:00:00Z"}, {"technician", "planner", "admin"}),
        ("/rehearsal/reset", "POST", None, {"technician", "planner", "admin"}),
        ("/stream/speed?speed=600", "POST", None, {"admin"}),
        ("/stream/pause?paused=false", "POST", None, {"admin"}),
    ]
    clients = {role: authenticated_client(role) for role in ROLES}
    insufficient_responses = []

    for path, method, body, allowed_roles in cases:
        for role in ROLES:
            if role in allowed_roles:
                continue
            response = clients[role].request(method, path, json=body)
            insufficient_responses.append((method, path, role, response))
            assert response.status_code == 403, (method, path, role, response.text)
            assert set(response.json()) == {"error"}
            assert response.json()["error"]["code"] == "forbidden"

    assert len(insufficient_responses) >= 16


def test_operator_cannot_approve_and_unknown_ids_are_not_materialised() -> None:
    operator = authenticated_client("operator")
    planner = authenticated_client("planner")

    assert operator.post("/approvals/security-approval/approve", json={}).status_code == 403
    response = planner.post("/approvals/security-approval/approve", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "approval_not_found"


def test_login_uses_an_http_only_strict_cookie_and_required_claims() -> None:
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": "planner", "password": SEED_PASSWORD})

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert f"{COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Max-Age=28800" in set_cookie

    token = response.cookies[COOKIE_NAME]
    principal = verify_token(token)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert principal.username == "planner"
    assert principal.role == "planner"
    assert set(("sub", "role", "jti", "exp")).issubset(claims)
    assert claims["sub"] == "planner"
    assert claims["role"] == "planner"


def test_expired_and_tampered_tokens_use_the_frozen_401_envelope() -> None:
    expired = create_access_token("planner", "planner", now=datetime.now(UTC) - timedelta(hours=9), ttl=timedelta(hours=1))
    valid = create_access_token("planner", "planner")
    token_parts = valid.split(".")
    signature = token_parts[2]
    token_parts[2] = f"{'a' if signature[0] != 'a' else 'b'}{signature[1:]}"
    tampered = ".".join(token_parts)

    for label, token in (("expired", expired), ("tampered", tampered)):
        response = TestClient(app).get("/machines", headers={"Cookie": f"{COOKIE_NAME}={token}"})
        assert response.status_code == 401, (label, response.text)
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message", "trace_id"}
        assert "Traceback" not in response.text


def test_all_api_routes_are_guarded_except_documented_public_routes() -> None:
    public = {"/health", "/auth/login", "/docs", "/openapi.json"}
    api_routes = [route for route in app.routes if isinstance(route, APIRoute)]

    assert {route.path for route in app.routes if route.path in public} == public
    assert {route.path for route in api_routes if route.path in public} == {"/health", "/auth/login"}
    for route in api_routes:
        if route.path not in public:
            assert route.dependant.dependencies, f"{route.path} has no authentication dependency"

    assert TestClient(app).get("/health").status_code == 200
    assert TestClient(app).get("/docs").status_code == 200
    assert TestClient(app).get("/openapi.json").status_code == 200
    assert TestClient(app).get("/metrics").status_code == 401


def test_seeded_passwords_are_argon2id_hashes_and_not_plaintext() -> None:
    seed_users()
    with session_scope() as session:
        users = list(session.query(User).all())

    assert {user.username for user in users} >= set(ROLES)
    for user in users:
        assert user.argon2_hash.startswith("$argon2id$")
        assert user.argon2_hash != SEED_PASSWORD
        assert SEED_PASSWORD not in user.argon2_hash
