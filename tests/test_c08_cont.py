from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "ui" / "src"
APP_SOURCE = (UI_ROOT / "App.tsx").read_text(encoding="utf-8")
SCREEN_SOURCE = (UI_ROOT / "screens" / "screens.tsx").read_text(encoding="utf-8")
STREAM_SOURCE = (UI_ROOT / "lib" / "stream.tsx").read_text(encoding="utf-8")
THEME_SOURCE = (UI_ROOT / "styles" / "theme.css").read_text(encoding="utf-8")
API_SOURCE = (ROOT / "api" / "main.py").read_text(encoding="utf-8")


def test_c08_cont_ui_has_no_fixture_dependency() -> None:
    ui_files = [path for path in UI_ROOT.rglob("*") if path.is_file()]

    assert ui_files
    assert all("fixture" not in path.read_text(encoding="utf-8").lower() for path in ui_files)


def test_sse_contract_preserves_last_event_and_has_ten_second_fallback() -> None:
    assert 'new EventSource(streamUrl(lastEventIdRef.current))' in STREAM_SOURCE
    assert "last_event_id" in STREAM_SOURCE
    assert 'const POLLING_INTERVAL_MS = 10_000;' in STREAM_SOURCE
    assert 'const POLLING_QUERY_KEYS = [["machines"], ["alerts"], ["health"], ["live-telemetry"]]' in STREAM_SOURCE
    assert 'setState("reconnecting")' in STREAM_SOURCE
    assert '"telemetry" | "alert" | "run_state"' in STREAM_SOURCE
    assert 'request.headers.get("last-event-id") or last_event_id' in API_SOURCE


def test_health_strip_keeps_six_labelled_dependency_states_when_health_fails() -> None:
    assert 'const HEALTH_DEPENDENCIES = ["db", "tsdb", "models", "erp", "llm", "stream"];' in APP_SOURCE
    assert 'health.isPending ? HEALTH_DEPENDENCIES.map' in APP_SOURCE
    assert 'health.data?.dependencies.find((item) => item.name === name)' in APP_SOURCE
    assert 'const label = dependency?.status ?? "unavailable";' in APP_SOURCE
    assert 'className="dependency-status"' in APP_SOURCE
    assert 'Dependency status unavailable' not in APP_SOURCE


def test_health_endpoint_returns_the_six_dependency_contract() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    dependencies = response.json()["dependencies"]
    assert [dependency["name"] for dependency in dependencies] == [
        "db",
        "tsdb",
        "models",
        "erp",
        "llm",
        "stream",
    ]
    assert all(dependency["status"] in {"up", "degraded", "down"} for dependency in dependencies)
    assert all(dependency["breaker_state"] in {"closed", "open", "half_open"} for dependency in dependencies)
    llm = next(dependency for dependency in dependencies if dependency["name"] == "llm")
    assert {"reachable", "model_name", "provider", "tls_verified"} <= set(llm)


def test_health_marks_keyless_llm_as_degraded_without_breaking_local_fallback(monkeypatch) -> None:
    monkeypatch.setattr(api_main.settings.llm, "api_key", "")

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    payload = response.json()
    llm = next(dependency for dependency in payload["dependencies"] if dependency["name"] == "llm")
    assert payload["status"] == "degraded"
    assert llm["status"] == "degraded"
    assert llm["reachable"] is False
    assert llm["breaker_state"] == "closed"
    assert "offline deterministic fallback active" in llm["detail"]


def test_seeded_demo_exposes_real_pump_07_pending_approval(monkeypatch, tmp_path) -> None:
    plant_path = tmp_path / "plant.db"
    shutil.copy2(ROOT / "data" / "plant.db", plant_path)
    monkeypatch.setattr(api_main.settings, "db_url", f"sqlite:///{plant_path}")
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "planner", "password": "fixture"}).status_code == 200

    response = client.get("/approvals?state=pending")

    assert response.status_code == 200
    assert any(item["machine_id"] == "PUMP-07" and item["state"] == "pending" and item["payload_hash"] for item in response.json())


def test_erp_degradation_is_visible_in_one_health_poll(monkeypatch) -> None:
    async def degraded_erp():
        return "degraded", 1.0, "ERP health probe failed: stopped", "open"

    monkeypatch.setattr("api.main._check_erp", degraded_erp)
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    erp = next(dependency for dependency in response.json()["dependencies"] if dependency["name"] == "erp")
    assert erp["status"] == "degraded"
    assert erp["breaker_state"] == "open"
    assert "stopped" in erp["detail"]


def test_metrics_returns_all_seven_observability_families() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    response = client.get("/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert {
        "requests",
        "errors_by_code",
        "agent_latencies",
        "guardrail_rejections_by_rule",
        "llm_tokens",
        "dlq_depth",
        "circuit_breaker_trips",
    } <= set(payload)
    assert payload["requests"] >= 1
    assert {"prompt", "completion", "cost_inr"} <= set(payload["llm_tokens"])


def test_all_routes_and_panels_have_loading_or_honest_empty_paths() -> None:
    for route in (
        'path="/floor"',
        'path="/machines/:machineId"',
        'path="/diagnosis/:diagnosisId"',
        'path="/schedule"',
        'path="/ledger"',
        'path="/trust"',
    ):
        assert route in APP_SOURCE

    for marker in (
        "MachineTileSkeleton",
        "AlertSkeleton",
        "MachineDetailSkeleton",
        "TelemetrySkeleton",
        "DiagnosisSkeleton",
        "AgentTraceLoading",
        "calendar-grid--skeleton",
        "approval-card--skeleton",
        "LedgerSkeleton",
        "TrustPanelSkeleton",
        "No machines returned",
        "No open alerts",
        "No telemetry in this window",
        "No approvals waiting",
        "No policy data returned",
        "No injection findings",
    ):
        assert marker in SCREEN_SOURCE


def test_skeleton_geometry_matches_the_stable_panel_dimensions() -> None:
    assert ".channel-panel { min-height: 220px;" in THEME_SOURCE
    assert ".channel-panel--skeleton" in THEME_SOURCE
    assert ".channel-panel--skeleton {" in THEME_SOURCE
    assert ".trust-panel { min-height: 220px;" in THEME_SOURCE
    assert ".trust-panel-skeleton {" in THEME_SOURCE
    assert ".dependency { display: inline-flex; min-width: 110px;" in THEME_SOURCE
    assert 'Skeleton width="110px" height="12px"' in APP_SOURCE
