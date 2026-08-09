from __future__ import annotations

from fastapi.testclient import TestClient

from api.auth import SEED_PASSWORD
from api.main import RATE_LIMITER, app
from api.security import SECURITY_HEADERS


def authenticated_client(username: str = "technician") -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": username, "password": SEED_PASSWORD})
    assert response.status_code == 200, response.text
    return client


def test_diagnose_token_bucket_returns_429_on_the_eleventh_request() -> None:
    RATE_LIMITER.clear()
    client = authenticated_client()
    responses = [client.post("/machines/PUMP-01/diagnose", json={"force_refresh": False}) for _ in range(11)]
    RATE_LIMITER.clear()

    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429
    assert set(responses[10].json()) == {"error"}
    assert responses[10].json()["error"]["code"] == "rate_limited"
    assert responses[10].headers["Retry-After"] == "60"


def test_security_headers_are_present_on_normal_and_error_responses() -> None:
    RATE_LIMITER.clear()
    client = authenticated_client()
    responses = [
        client.get("/health"),
        client.get("/machines"),
        client.get("/machines/UNKNOWN"),
        client.get("/audit/verify"),
        client.get("/openapi.json"),
    ]

    for response in responses:
        assert all(response.headers.get(name) == value for name, value in SECURITY_HEADERS.items())


def test_catch_all_error_is_safe_for_browser_clients() -> None:
    async def deliberate_failure():
        raise ZeroDivisionError("SELECT password FROM users")

    app.add_api_route("/_security-test/boom", deliberate_failure, methods=["GET"])
    route = next(route for route in app.routes if getattr(route, "path", None) == "/_security-test/boom")
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/_security-test/boom")
    finally:
        app.router.routes.remove(route)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "Traceback" not in response.text
    assert "SELECT password" not in response.text
    assert set(response.json()["error"]) == {"code", "message", "trace_id"}
    assert all(response.headers.get(name) == value for name, value in SECURITY_HEADERS.items())


def test_body_limits_reject_large_ingest_and_other_requests() -> None:
    RATE_LIMITER.clear()
    client = TestClient(app)
    ingest_response = client.post("/ingest/batch", content=b"x" * (3 * 1024 * 1024), headers={"Content-Type": "application/json"})
    other_response = client.post("/auth/login", content=b"x" * (100 * 1024), headers={"Content-Type": "application/json"})

    assert ingest_response.status_code == 413
    assert other_response.status_code == 413
    assert ingest_response.json()["error"]["code"] == "request_too_large"
    assert other_response.json()["error"]["code"] == "request_too_large"


def test_cors_allows_only_the_local_ui_origin() -> None:
    client = TestClient(app)
    allowed = client.options(
        "/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    rejected = client.options(
        "/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_default_uvicorn_binding_is_loopback_only() -> None:
    import uvicorn

    assert uvicorn.Config("api.main:app").host == "127.0.0.1"
