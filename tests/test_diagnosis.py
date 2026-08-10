from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from agents.coordinator_agent import CoordinatorAgent
from api.main import app
from llm.client import LLMResponse


client = TestClient(app)
assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200


def test_diagnosis_payload_contains_grounded_citations_actions_and_fusion_terms() -> None:
    response = client.post("/machines/PUMP-07/diagnose", json={"force_refresh": False})

    assert response.status_code == 200
    diagnosis = response.json()["diagnosis"]
    assert diagnosis["generated"] is False
    assert diagnosis["model"] == "deterministic-template"
    assert diagnosis["provider"] == "local"
    assert diagnosis["explanation"]
    assert diagnosis["fallback_reason"] == "offline_mode"
    assert diagnosis["limits"]
    assert len(diagnosis["risk_terms"]) == 5
    assert round(sum(diagnosis["risk_terms"].values()), 2) == diagnosis["risk"]
    assert {citation["id"] for citation in diagnosis["citations"]} >= {"S1", "S2", "M1", "M3"}
    sensor = next(citation for citation in diagnosis["citations"] if citation["id"] == "S2")
    assert sensor["channel"] == "temp_bearing_c"
    assert sensor["t0"] < sensor["t1"]
    work_order = next(citation for citation in diagnosis["citations"] if citation["id"] == "M3")
    assert work_order["fault_code"]
    assert work_order["technician_note"]
    assert work_order["cost_inr"] > 0
    assert "cost_gbp" not in work_order
    assert diagnosis["recommended_actions"][0]["action"]
    assert diagnosis["recommended_actions"][0]["citations"]
    trace = client.get(f"/runs/{response.json()['trace_id']}")
    assert trace.status_code == 200
    assert {run["agent"] for run in trace.json()["runs"]} >= {
        "sensor", "maintenance_log", "fuse", "coordinator", "guardrail", "route",
    }


class _GeneratedDiagnosisClient:
    provider = "test-gemini"

    def chat(self, _messages: object, _model: object) -> LLMResponse:
        payload = {
            "fault_diagnosis": "Bearing wear is the most likely fault based on current and historical evidence. [S1][M3]",
            "reasoning_steps": [
                "The prediction identifies bearing wear as the leading fault mode. [S1]",
                "Recent temperature evidence supports a developing mechanical condition. [S2]",
                "The maintenance history shows a repeated bearing replacement pattern. [M1][M3]",
            ],
            "facts": ["The evidence bundle contains two current sensor windows. [S1][S2]"],
            "assumptions": ["The supplied operating context remains representative."],
            "recommended_actions": [{
                "action": "Inspect the drive-end bearing",
                "priority": "high",
                "rationale": "The current vibration evidence and repeat repair history justify inspection. [S1][M3]",
                "parts": ["Drive-end bearing kit"],
                "est_labour_hours": 2.5,
                "window": "Next planned maintenance window",
                "citations": ["S1", "M3"],
            }],
            "limits": "The explanation is limited to the supplied telemetry and work-order evidence.",
            "citations": ["S1", "S2", "M1", "M3"],
        }
        return LLMResponse(
            text=json.dumps(payload),
            prompt_tokens=120,
            completion_tokens=90,
            model="test-generated-model",
            latency_ms=12.0,
            provider=self.provider,
        )


def test_live_diagnosis_route_surfaces_the_generated_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main.settings.llm, "api_key", "test-key")
    monkeypatch.setattr(
        api_main,
        "CoordinatorAgent",
        lambda **_kwargs: CoordinatorAgent(llm_client=_GeneratedDiagnosisClient(), llm_api_key="test-key"),
    )

    response = client.post("/machines/PUMP-07/diagnose", json={"force_refresh": True})

    assert response.status_code == 200
    payload = response.json()
    diagnosis = payload["diagnosis"]
    assert diagnosis["generated"] is True
    assert diagnosis["offline_mode"] is False
    assert diagnosis["model"] == "test-generated-model"
    assert diagnosis["provider"] == "test-gemini"
    assert diagnosis["fallback_reason"] is None
    assert diagnosis["explanation"].startswith("Bearing wear is the most likely fault")
    trace = client.get(f"/runs/{payload['trace_id']}")
    assert trace.status_code == 200
    trace_runs = trace.json()["runs"]
    assert {run["agent"] for run in trace_runs} >= {"sensor", "maintenance_log", "fuse", "coordinator", "guardrail", "route"}
    coordinator = next(run for run in trace_runs if run["agent"] == "coordinator")
    assert coordinator["state"] == "completed"
    assert coordinator["completion_tokens"] == 90
    guardrail = next(run for run in trace_runs if run["agent"] == "guardrail")
    assert guardrail["guardrail_verdict"] == "accepted"


def test_diagnosis_ui_prioritises_explanation_and_removes_demo_navigation() -> None:
    root = Path(__file__).resolve().parents[1]
    screen = (root / "ui" / "src" / "screens" / "screens.tsx").read_text(encoding="utf-8")
    app_source = (root / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "LLM-GENERATED EXPLANATION" in screen
    assert "diagnosis.explanation" in screen
    assert "Evidence and reasoning" in screen
    assert "Technical trace and provenance" in screen
    assert "Explain current risk" in screen
    assert 'label: "Machine Detail"' not in app_source
    assert 'label: "Diagnosis"' not in app_source
