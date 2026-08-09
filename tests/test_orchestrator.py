from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from agents.base import AgentResult, RunContext, load_agent_runs
from agents.coordinator_agent import CoordinatorAgent
from agents.guardrail_agent import GuardrailAgent
from agents.orchestrator import DiagnosisRun, OrchestratorConfig, RunState


def _sensor_output() -> dict[str, Any]:
    return {
        "p_failure_24h": 0.10,
        "rul_p50": 30.0,
        "mode_probs": {"bearing_wear": 0.8},
        "quality": {"completeness": 0.98},
        "evidence_count": 3,
        "evidence": [{"id": "S1", "channel": "vibration_rms_mm_s", "value": 4.0}],
    }


def _history_output() -> dict[str, Any]:
    return {
        "history_prior": 0.1,
        "dominant_mode": "bearing_wear",
        "evidence": [{"id": "M1", "fault_code": "BRG-01", "text": "bearing inspection"}],
    }


class StaticAgent:
    def __init__(self, name: str, output: dict[str, Any], order: list[str] | None = None) -> None:
        self.name = name
        self.version = "v1"
        self.model_name = "test"
        self.provider = "test"
        self.output = output
        self.order = order

    def run(self, _: RunContext) -> AgentResult:
        if self.order is not None:
            self.order.append(self.name)
        return AgentResult(ok=True, output=self.output, evidence=list(self.output.get("evidence", [])), confidence=0.9, model_name=self.model_name, provider=self.provider)


def _agents(database_url: str, *, sensor: Any | None = None, maintenance: Any | None = None, coordinator: Any | None = None, guardrail: Any | None = None, **kwargs: Any) -> DiagnosisRun:
    return DiagnosisRun(
        sensor or StaticAgent("sensor", _sensor_output()),
        maintenance or StaticAgent("maintenance_log", _history_output()),
        coordinator or CoordinatorAgent(llm_api_key=""),
        guardrail or GuardrailAgent(database_url=database_url),
        database_url=database_url,
        **kwargs,
    )


def _run(run: DiagnosisRun, trace_id: str = "trace-orchestrator-test"):
    return asyncio.run(run.execute(RunContext(trace_id=trace_id, payload={"machine_meta": {"criticality": "minor"}})))


def test_happy_path_persists_one_trace_and_emits_ordered_states(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    published: list[dict[str, Any]] = []
    result = _run(_agents(database_url, event_sink=published.append))

    assert result.status == "completed"
    assert result.route["action"] == "auto_log"
    assert result.state == RunState.DONE
    assert [item["state"] for item in published] == [state.value for state in (RunState.INIT, RunState.FANOUT, RunState.FUSE, RunState.DIAGNOSE, RunState.REFLECT, RunState.ROUTE, RunState.DONE)]
    rows = load_agent_runs(result.trace_id, database_url=database_url)
    assert [row.agent for row in rows] == ["sensor", "maintenance_log", "fuse", "coordinator", "guardrail", "route"]
    assert {row.trace_id for row in rows} == {result.trace_id}
    assert rows[2].latency_ms >= 0
    assert rows[-1].latency_ms >= 0


def test_failed_sensor_is_partial_and_coordinator_is_told_sensor_is_missing(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    captured: list[dict[str, Any]] = []
    template = CoordinatorAgent(llm_api_key="")

    class FailedSensor(StaticAgent):
        def run(self, _: RunContext) -> AgentResult:
            return AgentResult(ok=False, output={"reason": "sensor_unavailable"}, errors=["sensor unavailable"])

    class CapturingCoordinator:
        name = "coordinator"
        version = "v1"
        model_name = "deterministic-template"
        provider = "local"

        def run(self, context: RunContext) -> AgentResult:
            captured.append(dict(context.payload))
            return template.run(context)

    result = _run(_agents(database_url, sensor=FailedSensor("sensor", {}), coordinator=CapturingCoordinator()))

    assert result.status == "completed"
    assert result.missing_arms == ["sensor"]
    assert result.fusion_result["band"] == "NODATA"
    assert captured[0]["missing_arms"] == ["sensor"]
    assert "sensor arm is unavailable" in captured[0]["missing_evidence"][0]


def test_guardrail_rejection_retries_coordinator_once_then_uses_template(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    template = CoordinatorAgent(llm_api_key="")

    class RetriedCoordinator:
        name = "coordinator"
        version = "v1"
        model_name = "test-model"
        provider = "test"

        def __init__(self) -> None:
            self.calls = 0
            self.contexts: list[RunContext] = []

        def run(self, context: RunContext) -> AgentResult:
            self.calls += 1
            self.contexts.append(context)
            result = template.run(context)
            return AgentResult(ok=True, output={**result.output, "generated": True, "model": "test-model"}, confidence=result.confidence, model_name="test-model", provider="test")

    class RejectingGuardrail:
        name = "guardrail"
        version = "v1"
        model_name = "n/a"
        provider = "deterministic"

        def __init__(self) -> None:
            self.calls = 0

        def run(self, _: RunContext) -> AgentResult:
            self.calls += 1
            return AgentResult(ok=True, output={"accepted": False, "rejections": [{"message": "test rejection"}]})

    coordinator = RetriedCoordinator()
    guardrail = RejectingGuardrail()
    events: list[dict[str, Any]] = []
    result = _run(_agents(database_url, coordinator=coordinator, guardrail=guardrail, event_sink=events.append))

    assert coordinator.calls == 2
    assert guardrail.calls == 2
    assert result.retry_count == 1
    assert result.template_used is True
    assert result.diagnosis_result.model_name == "deterministic-template"
    assert coordinator.contexts[1].payload["reflection_rejections"] == ["test rejection"]
    assert "test rejection" in str(coordinator.contexts[1].payload["evidence_bundle"])
    assert any(item.get("retry") is True for item in events if item["state"] == RunState.DIAGNOSE)


def test_hanging_agent_is_cancelled_per_attempt_without_blocking_run(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"

    class HangingSensor(StaticAgent):
        def run(self, _: RunContext) -> AgentResult:
            time.sleep(0.20)
            return AgentResult(ok=True, output=_sensor_output())

    started = time.perf_counter()
    result = _run(
        _agents(
            database_url,
            sensor=HangingSensor("sensor", {}),
            config=OrchestratorConfig(agent_timeout_s=0.05, run_timeout_s=0.5, max_agent_attempts=2),
        )
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert result.status == "completed"
    assert result.missing_arms == ["sensor"]
    assert result.sensor_result.output["reason"] == "agent_timeout"


def test_sequential_contingency_runs_arms_serially(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    order: list[str] = []
    assert OrchestratorConfig.from_argv(["--sequential"]).sequential is True
    result = _run(
        _agents(
            database_url,
            sensor=StaticAgent("sensor", _sensor_output(), order),
            maintenance=StaticAgent("maintenance_log", _history_output(), order),
            sequential=True,
        )
    )

    assert result.status == "completed"
    assert order == ["sensor", "maintenance_log"]
