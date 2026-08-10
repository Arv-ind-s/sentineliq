from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app


ROOT = Path(__file__).resolve().parents[1]
SCREEN_SOURCE = (ROOT / "ui" / "src" / "screens" / "screens.tsx").read_text(encoding="utf-8")
TYPE_SOURCE = (ROOT / "ui" / "src" / "types" / "api.ts").read_text(encoding="utf-8")


def test_trust_screen_renders_live_sources_and_acceptance_markers() -> None:
    for marker in (
        'fetchJson<MetricsResponse>("/metrics")',
        'fetchJson<HealthResponse>("/health")',
        'fetchJson<DlqItem[]>("/ingest/dlq")',
        'data-testid={`${id}-target-line`}',
        'target={85}',
        'target={20}',
        "guardrail_rejections_by_rule",
        "Force deterministic templates",
        "Offline diagnosis mode active",
        "tls_verified",
        'currency: "INR"',
    ):
        assert marker in SCREEN_SOURCE
    assert "LlmOfflineResponse" in TYPE_SOURCE


def test_offline_control_is_available_through_the_same_origin_dev_proxy() -> None:
    vite_source = (ROOT / "ui" / "vite.config.ts").read_text(encoding="utf-8")
    api_source = (ROOT / "ui" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")

    assert 'proxy: { "/api": apiProxy }' in vite_source
    assert 'path.replace(/^\\/api/, "")' in vite_source
    assert "fetch(apiPath(path)" in api_source


def test_eval_report_exposes_the_eight_planted_injection_notes() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    response = client.get("/eval/report")

    assert response.status_code == 200
    injection = response.json()["summary"]["injection"]
    assert injection["total"] == 8
    assert len(injection["fixtures"]) == 8
    assert len(injection["notes"]) == 8


def test_offline_toggle_is_authenticated_and_stamps_diagnosis() -> None:
    client = TestClient(app)
    assert client.post("/llm/offline", json={"enabled": True}).status_code == 401
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    try:
        enabled = client.post("/llm/offline", json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        assert enabled.json()["effective_offline"] is True

        diagnosis = client.post("/machines/PUMP-07/diagnose", json={"force_refresh": True})
        assert diagnosis.status_code == 200
        assert diagnosis.json()["diagnosis"]["offline_mode"] is True
        assert diagnosis.json()["diagnosis"]["model"] == "deterministic-template"
    finally:
        assert client.post("/llm/offline", json={"enabled": False}).status_code == 200
