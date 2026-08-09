from __future__ import annotations

import json
from pathlib import Path

import pytest

import agents.coordinator_agent as coordinator_module
from agents.base import RunContext
from agents.coordinator_agent import CoordinatorAgent, CoordinatorOutput
from agents.maintenance_log_agent import MaintenanceLogAgent
from core.retrieval import MaintenanceLogRetriever
from llm.client import LLMResponse


ERP_DB = Path(__file__).resolve().parents[1] / "erp" / "erp.db"


def _payload(*, evidence: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "sensor_result": {
            "p_failure_24h": 0.87,
            "rul_p50": 4.2,
            "mode_probs": {"bearing_wear": 0.9},
            "quality": {"completeness": 0.97},
            "evidence": [{"id": "S1", "channel": "vibration_rms_mm_s", "value": 10.2}],
        },
        "history_result": {
            "history_prior": 0.62,
            "dominant_mode": "bearing_wear",
            "evidence": [{"id": "M1", "fault_code": "BRG-01", "text": "bearing repair"}],
        },
        "machine_meta": {"criticality": "major", "hourly_downtime_cost_gbp": 1800},
        **({"evidence_bundle": evidence} if evidence is not None else {}),
    }


def _llm_payload() -> dict[str, object]:
    return {
        "fault_diagnosis": "The evidence supports a bearing-wear diagnosis.",
        "reasoning_steps": [
            "Vibration evidence is elevated. [S1]",
            "Maintenance history shows a matching repair pattern. [M1]",
            "The two evidence sources support the same maintenance class. [S1][M1]",
        ],
        "facts": ["The supplied vibration observation is linked to the current window. [S1]"],
        "assumptions": ["The current operating load remains representative."],
        "recommended_actions": [
            {
                "action": "Inspect the drive-end bearing",
                "priority": "high",
                "rationale": "The current observation aligns with the prior repair. [S1][M1]",
                "parts": ["Bearing kit"],
                "est_labour_hours": 2.0,
                "window": "model cannot override this",
                "citations": ["S1", "M1"],
            }
        ],
        "limits": "The supplied evidence does not establish the exact failed component.",
        "citations": ["S1", "M1"],
    }


class FakeLLM:
    provider = "test-provider"

    def __init__(self, text: str | None = None) -> None:
        self.calls = 0
        self.text = text or json.dumps(_llm_payload())

    def chat(self, messages, model_cfg) -> LLMResponse:
        self.calls += 1
        return LLMResponse(self.text, 100, 40, "openai-compatible-chat", 2.0, provider=self.provider)


def test_fusion_runs_before_llm_and_fixed_numbers_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    real_fuse = coordinator_module.fuse

    def spy_fuse(*args, **kwargs):
        order.append("fusion")
        return real_fuse(*args, **kwargs)

    llm = FakeLLM()
    original_chat = llm.chat

    def ordered_chat(messages, model_cfg):
        order.append("llm")
        return original_chat(messages, model_cfg)

    llm.chat = ordered_chat
    monkeypatch.setattr(coordinator_module, "fuse", spy_fuse)
    result = CoordinatorAgent(llm_client=llm, llm_api_key="test-key").run(RunContext(payload=_payload()))

    assert order == ["fusion", "llm"]
    assert llm.calls == 1
    assert result.output["model"] == "openai-compatible-chat"
    assert result.output["provider"] == "test-provider"
    assert result.output["generated"] is True
    assert result.output["prompt_version"] == "v1"
    assert result.output["risk"] == 73.06
    assert result.output["band"] == "HIGH"
    assert result.output["confidence"] == 0.23926667
    assert result.output["autonomy"] == "A0"
    assert result.output["action_class"] == "repair"
    assert result.output["recommended_actions"][0]["window"] == "After planner approval"
    CoordinatorOutput.model_validate(result.output)


@pytest.mark.parametrize("prefix", ["```json\n", "Here is the JSON:\n"])
def test_fenced_and_preamble_responses_are_tolerated(prefix: str) -> None:
    suffix = "\n```" if prefix.startswith("```") else ""
    llm = FakeLLM(prefix + json.dumps(_llm_payload()) + suffix)
    result = CoordinatorAgent(llm_client=llm, llm_api_key="test-key").run(RunContext(payload=_payload()))

    assert result.ok is True
    assert result.output["generated"] is True
    assert result.fallback_used is False


def test_gateway_style_grouped_and_context_citations_are_resolved() -> None:
    payload = _llm_payload()
    payload["reasoning_steps"] = [
        "Vibration evidence supports the prediction [S1].",
        "Maintenance history shows a repeated repair pattern [history_result].",
        "Both evidence arms support inspection [S1, M1].",
    ]
    payload["facts"] = ["The maintenance aggregate records a prior bearing repair [history_result]."]
    action = dict(payload["recommended_actions"][0])
    action.pop("parts")
    action["rationale"] = "The sensor and maintenance context support inspection [sensor_result][history_result]."
    action["citations"] = ["sensor_result", "history_result"]
    payload["recommended_actions"] = [action]

    result = CoordinatorAgent(llm_client=FakeLLM(json.dumps(payload)), llm_api_key="test-key").run(RunContext(payload=_payload()))

    assert result.output["generated"] is True
    assert result.output["recommended_actions"][0]["parts"] == []
    assert result.output["recommended_actions"][0]["citations"] == ["S1", "M1"]
    assert result.output["reasoning_steps"][1].endswith("[M1].")
    assert "[S1][M1]" in result.output["reasoning_steps"][2]


def test_gateway_reasoning_without_trailing_tags_is_grounded_from_known_evidence() -> None:
    payload = _llm_payload()
    payload["reasoning_steps"] = [
        "Sensor vibration supports the current prediction.",
        "Maintenance history shows a repeated repair pattern.",
        "Fusion reports agreement between sensor and history evidence.",
    ]

    result = CoordinatorAgent(llm_client=FakeLLM(json.dumps(payload)), llm_api_key="test-key").run(RunContext(payload=_payload()))

    assert result.output["generated"] is True
    assert result.fallback_used is False
    assert result.output["reasoning_steps"][0].endswith("[S1].")
    assert result.output["reasoning_steps"][1].endswith("[M1].")
    assert result.output["reasoning_steps"][2].endswith("[S1][M1].")


def test_missing_key_uses_complete_deterministic_template_and_has_no_erp_write_tool() -> None:
    llm = FakeLLM()
    agent = CoordinatorAgent(llm_client=llm, llm_api_key="")
    result = agent.run(RunContext(payload=_payload()))

    assert result.ok is True
    assert result.output["generated"] is False
    assert result.output["model"] == "deterministic-template"
    assert result.output["limits"]
    assert 1 <= len(result.output["recommended_actions"]) <= 4
    assert llm.calls == 0
    with pytest.raises(AttributeError):
        getattr(agent, "create_work_order")
    CoordinatorOutput.model_validate(result.output)


def test_evidence_bundle_is_capped_and_records_low_rank_items() -> None:
    evidence = [
        {"id": f"S{index}", "rank": index, "text": "grounded vibration observation " * 450}
        for index in range(1, 21)
    ]
    llm = FakeLLM()
    result = CoordinatorAgent(llm_client=llm, llm_api_key="", max_context_tokens=6000).run(
        RunContext(payload=_payload(evidence=evidence))
    )

    assert result.output["evidence_tokens"] <= 6000
    assert result.output["dropped_evidence"]
    assert "S20" in result.output["dropped_evidence"]
    assert len(result.evidence) < 20


def test_coordinator_and_maintenance_log_use_at_most_two_llm_calls() -> None:
    class SharedLLM:
        provider = "test-provider"

        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, model_cfg) -> LLMResponse:
            self.calls += 1
            if "fixed_fusion" in str(messages[0]["content"]):
                text = json.dumps(_llm_payload())
            else:
                text = json.dumps(
                    {
                        "recurring_modes": ["bearing wear recurs [M1]"],
                        "effective_repairs": ["Bearing replaced [M1]"],
                        "ineffective_repairs": [],
                        "parts_needed": [],
                        "typical_downtime_min": 120,
                        "notes": ["Prior repair is recorded [M1]"],
                    }
                )
            return LLMResponse(text, 10, 20, "openai-compatible-chat", 1.0, provider=self.provider)

    class FakeERP:
        def search_workorders(self, machine_id: str, since=None, limit: int = 6):
            return [
                {
                    "workorder_id": "WO-001",
                    "asset_id": machine_id,
                    "fault_code": "BRG-01",
                    "description": "Replaced bearing",
                    "technician_notes": "Bearing replaced",
                    "created_at": "2025-01-01T00:00:00+00:00",
                    "cost_gbp": 1800,
                    "downtime_minutes": 120,
                    "lead_time_days": 2,
                }
            ]

        def parts_lookup(self, fault_code: str):
            return []

    llm = SharedLLM()
    CoordinatorAgent(llm_client=llm, llm_api_key="test-key").run(RunContext(payload=_payload()))
    MaintenanceLogAgent(
        erp_client=FakeERP(),
        retriever=MaintenanceLogRetriever(ERP_DB),
        llm_client=llm,
        llm_api_key="test-key",
        retry_delays_s=(0, 0, 0),
    ).run(RunContext(payload={"machine_id": "PUMP-07", "query": "bearing"}))

    assert llm.calls == 2
