"""Bounded diagnosis orchestration for the supervised agent pipeline.

The orchestrator owns sequencing and operational policy only.  Specialist
agents receive the original run context independently, deterministic fusion is
performed after fan-in, and the Coordinator is the only component that sees
both evidence arms.  ERP writes are intentionally outside this module; that
capability belongs to the approval task.
"""

from __future__ import annotations

import asyncio
import argparse
import inspect
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from agents.base import Agent, AgentResult, RunContext, TransientAgentError, persist_agent_run
from agents.coordinator_agent import CoordinatorAgent
from core.fusion import HistoryEvidence, MachineContext, SensorEvidence, explain, fuse
from llm.client import TransportError


logger = logging.getLogger(__name__)


class RunState(StrEnum):
    """Public state names emitted on the ``run_state`` SSE channel."""

    INIT = "INIT"
    FANOUT = "FANOUT"
    FUSE = "FUSE"
    DIAGNOSE = "DIAGNOSE"
    REFLECT = "REFLECT"
    ROUTE = "ROUTE"
    ACT = "ACT"
    DONE = "DONE"


@dataclass(frozen=True)
class OrchestratorConfig:
    """The fixed timing and execution policy for one diagnosis run."""

    agent_timeout_s: float = 20.0
    run_timeout_s: float = 45.0
    max_agent_attempts: int = 2
    sequential: bool = False

    @classmethod
    def from_argv(cls, argv: Sequence[str] | None = None) -> "OrchestratorConfig":
        """Build the contingency configuration from the documented flag."""

        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--sequential", action="store_true", help="run specialist arms serially")
        args, _ = parser.parse_known_args(argv)
        return cls(sequential=bool(args.sequential))

    def __post_init__(self) -> None:
        if self.agent_timeout_s <= 0 or self.run_timeout_s <= 0:
            raise ValueError("orchestrator timeouts must be positive")
        if self.run_timeout_s < self.agent_timeout_s:
            raise ValueError("run_timeout_s must cover at least one agent attempt")
        if self.max_agent_attempts < 1 or self.max_agent_attempts > 2:
            raise ValueError("max_agent_attempts must be one or two")


@dataclass(frozen=True)
class _Invocation:
    value: Any = None
    error: BaseException | None = None


@dataclass(frozen=True)
class AgentExecution:
    """An unpersisted agent result plus the operational facts around it."""

    result: AgentResult
    attempts: int
    transport_retry_used: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class _StageAgent:
    """Persistence identity for deterministic orchestration stages."""

    name: str
    version: str = "v1"
    model_name: str = "deterministic"
    provider: str = "local"


@dataclass
class DiagnosisRunResult:
    """Detached result of one complete or partial diagnosis run."""

    trace_id: str
    status: Literal["completed", "failed"]
    state: str
    sensor_result: AgentResult
    maintenance_result: AgentResult
    fusion_result: dict[str, Any]
    diagnosis_result: AgentResult
    guardrail_results: list[AgentResult] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    missing_arms: list[str] = field(default_factory=list)
    retry_count: int = 0
    template_used: bool = False

    @property
    def diagnosis(self) -> Any:
        return self.diagnosis_result.output

    @property
    def partial(self) -> bool:
        return bool(self.missing_arms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "status": self.status,
            "state": self.state,
            "sensor_result": self.sensor_result.as_dict(),
            "maintenance_result": self.maintenance_result.as_dict(),
            "fusion_result": _json_ready(self.fusion_result),
            "diagnosis_result": self.diagnosis_result.as_dict(),
            "guardrail_results": [item.as_dict() for item in self.guardrail_results],
            "route": _json_ready(self.route),
            "events": _json_ready(self.events),
            "errors": list(self.errors),
            "missing_arms": list(self.missing_arms),
            "retry_count": self.retry_count,
            "template_used": self.template_used,
        }

    def to_dict(self) -> dict[str, Any]:
        """Compatibility alias for callers serialising orchestration output."""

        return self.as_dict()

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(child) for child in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    return str(value)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _result_output(result: AgentResult) -> dict[str, Any]:
    return dict(result.output) if isinstance(result.output, Mapping) else {}


def _normalise_result(value: Any, agent: Agent) -> AgentResult:
    if isinstance(value, AgentResult):
        return value
    if isinstance(value, Mapping):
        return AgentResult(
            ok=bool(value.get("ok", False)),
            output=value.get("output", {}),
            evidence=list(value.get("evidence", [])),
            confidence=float(value.get("confidence", 0.0)),
            tokens=value.get("tokens", {}),
            latency_ms=float(value.get("latency_ms", 0.0)),
            errors=[str(item) for item in value.get("errors", [])],
            retries=int(value.get("retries", 0)),
            fallback_used=bool(value.get("fallback_used", False)),
            model_name=str(value.get("model_name", getattr(agent, "model_name", "n/a"))),
            provider=str(value.get("provider", getattr(agent, "provider", "n/a"))),
            cost_gbp=float(value.get("cost_gbp", 0.0)),
        )
    raise TypeError("agent.run() must return AgentResult or a result mapping")


def _retryable(error: BaseException) -> bool:
    return isinstance(error, (TransientAgentError, TimeoutError, ConnectionError, TransportError))


def _sensor_evidence(value: Mapping[str, Any], *, missing: bool) -> SensorEvidence:
    quality = value.get("quality", {})
    raw_evidence = value.get("evidence", [])
    evidence_count = len(raw_evidence) if isinstance(raw_evidence, Sequence) and not isinstance(raw_evidence, (str, bytes)) else 0
    modes = value.get("mode_probs", {})
    if isinstance(modes, Mapping) and modes:
        try:
            predicted_mode = str(max(modes.items(), key=lambda item: float(item[1]))[0])
        except (TypeError, ValueError):
            predicted_mode = str(value.get("predicted_mode", "unknown"))
    else:
        predicted_mode = str(value.get("predicted_mode", value.get("fault_mode", "unknown")))
    if isinstance(quality, Mapping):
        quality_value = quality.get("completeness", value.get("data_quality", 0.0))
    else:
        quality_value = value.get("data_quality", 0.0)
    try:
        data_quality = float(quality_value or 0.0)
    except (TypeError, ValueError):
        data_quality = 0.0
    return SensorEvidence(
        p_failure_24h=float(value.get("p_failure_24h", value.get("p_failure", 0.0)) or 0.0),
        rul_p50_days=float(value.get("rul_p50", value.get("rul_p50_days", value.get("rul_days", 0.0))) or 0.0),
        predicted_mode=predicted_mode,
        data_quality=data_quality,
        evidence_count=int(value.get("evidence_count", evidence_count)),
        required_channel_suspect=bool(value.get("required_channel_suspect", False) or missing),
        sensor_drift_signal=value.get("sensor_drift_signal"),
    )


def _history_evidence(value: Mapping[str, Any]) -> HistoryEvidence:
    summary = value.get("summary", value)
    recurring = summary.get("recurring_modes", []) if isinstance(summary, Mapping) else []
    dominant = value.get("dominant_mode")
    if dominant is None and isinstance(summary, Mapping):
        dominant = summary.get("dominant_mode", summary.get("dominant_fault_mode"))
    return HistoryEvidence(
        history_prior=float(value.get("history_prior", 0.0) or 0.0),
        dominant_mode=str(dominant) if dominant else None,
        repeat_failure=bool(value.get("repeat_failure", False)),
        repeat_failure_count=int(value.get("repeat_failure_count", len(recurring) if recurring else 0)),
        fault_code_occurrences_days=tuple(float(item) for item in value.get("fault_code_occurrences_days", ())),
        transducer_replacements_90d=int(value.get("transducer_replacements_90d", 0)),
    )


def _fusion_output(decision: Any) -> dict[str, Any]:
    return {
        "risk": decision.risk,
        "band": decision.band,
        "agreement": decision.agreement,
        "confidence": decision.confidence,
        "autonomy": decision.autonomy,
        "action_class": decision.action_class,
        "prediction_status": decision.prediction_status,
        "hard_rules": list(decision.hard_rules),
        "alerts": [_json_ready(item) for item in decision.alerts],
        "terms": explain(decision),
        "must_state_disagreement": decision.must_state_disagreement,
        "root_cause_escalation": decision.root_cause_escalation,
        "requires_approval": decision.requires_approval,
        "approver_role": decision.approver_role,
    }


def _evidence_bundle(
    sensor: Mapping[str, Any],
    history: Mapping[str, Any],
    fusion_result: Mapping[str, Any],
    sensor_evidence: Sequence[Any] = (),
    history_evidence: Sequence[Any] = (),
) -> dict[str, Any]:
    items: list[Any] = []
    for value in (sensor.get("evidence", []), history.get("evidence", []), sensor_evidence, history_evidence):
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            items.extend(value)
    return {
        "evidence": items,
        "sensor": sensor,
        "maintenance": history,
        "fusion": dict(fusion_result),
    }


def _route(decision: Mapping[str, Any]) -> dict[str, Any]:
    autonomy = str(decision.get("autonomy", "A0"))
    actions = {
        "A3": ("auto_log", None),
        "A2": ("dashboard_alert", "technician"),
        "A1": ("technician_ack", "technician"),
        "A0": ("planner_approval", "planner"),
    }
    action, required_role = actions.get(autonomy, actions["A0"])
    return {"autonomy": autonomy, "action": action, "required_role": required_role, "writes_blocked": autonomy == "A0"}


class DiagnosisRun:
    """Explicit, bounded ``INIT → ... → DONE`` diagnosis state machine."""

    def __init__(
        self,
        sensor_agent: Agent | None = None,
        maintenance_agent: Agent | None = None,
        coordinator_agent: Agent | None = None,
        guardrail_agent: Agent | None = None,
        *,
        sensor: Agent | None = None,
        maintlog_agent: Agent | None = None,
        maintenance_log_agent: Agent | None = None,
        coordinator: Agent | None = None,
        guardrail: Agent | None = None,
        database_url: str | None = None,
        log_path: str | None = None,
        sequential: bool | None = None,
        config: OrchestratorConfig | None = None,
        event_sink: Callable[..., Any] | Any | None = None,
        on_state: Callable[..., Any] | Any | None = None,
        event_publisher: Callable[..., Any] | Any | None = None,
    ) -> None:
        self.sensor_agent = sensor_agent or sensor
        self.maintenance_agent = maintenance_agent or maintlog_agent or maintenance_log_agent
        self.coordinator_agent = coordinator_agent or coordinator
        self.guardrail_agent = guardrail_agent or guardrail
        missing = [name for name, value in (("sensor_agent", self.sensor_agent), ("maintenance_agent", self.maintenance_agent), ("coordinator_agent", self.coordinator_agent), ("guardrail_agent", self.guardrail_agent)) if value is None]
        if missing:
            raise ValueError(f"missing required agents: {', '.join(missing)}")
        if config is not None and sequential is not None and config.sequential != sequential:
            raise ValueError("sequential conflicts with config.sequential")
        configured_sequential = bool(config.sequential) if config is not None else False
        if sequential is None:
            sequential = configured_sequential or os.getenv("ORCHESTRATOR_SEQUENTIAL", "").lower() in {"1", "true", "yes"}
        self.config = replace(config or OrchestratorConfig(), sequential=bool(sequential))
        self.database_url = database_url
        self.log_path = log_path
        self.event_sink = event_sink or on_state or event_publisher
        self.trace_id = ""
        self.state = RunState.INIT
        self.events: list[dict[str, Any]] = []
        self._event_sequence = 0

    async def _emit(self, state: RunState, **fields: Any) -> None:
        self.state = state
        self._event_sequence += 1
        data = {
            "trace_id": self.trace_id,
            "state": state.value,
            "sequence": self._event_sequence,
            "ts": datetime.now(UTC).isoformat(),
            **_json_ready(fields),
        }
        self.events.append({"event": "run_state", "data": data})
        sink = self.event_sink
        if sink is None:
            return
        try:
            if hasattr(sink, "publish"):
                result = sink.publish("run_state", data)
            else:
                target = sink
                try:
                    parameters = inspect.signature(target).parameters
                    result = target("run_state", data) if len(parameters) >= 2 else target(data)
                except (TypeError, ValueError):
                    result = target(data)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # event delivery must never stop diagnosis
            logger.warning("run_state_event_failed trace_id=%s error=%s", self.trace_id, type(exc).__name__)

    async def _invoke(self, agent: Agent, context: RunContext) -> Any:
        """Run sync or async agent code without letting a timed-out thread block shutdown."""

        loop = asyncio.get_running_loop()
        future: asyncio.Future[_Invocation] = loop.create_future()

        def complete(invocation: _Invocation) -> None:
            if not future.done():
                future.set_result(invocation)

        def notify(invocation: _Invocation) -> None:
            try:
                loop.call_soon_threadsafe(complete, invocation)
            except RuntimeError:
                # A timed-out daemon worker may finish after asyncio.run has
                # closed its loop.  Its result is intentionally discarded.
                return

        def worker() -> None:
            try:
                value = agent.run(context)
                if inspect.isawaitable(value):
                    value = asyncio.run(value)
                notify(_Invocation(value=value))
            except BaseException as exc:  # surface agent failures to the policy loop
                notify(_Invocation(error=exc))

        thread = threading.Thread(target=worker, name=f"agent-{getattr(agent, 'name', 'unknown')}-{self.trace_id[:8]}", daemon=True)
        thread.start()
        invocation = await future
        if invocation.error is not None:
            raise invocation.error
        return invocation.value

    async def _run_agent(self, agent: Agent, context: RunContext, *, timeout_s: float | None = None) -> AgentExecution:
        timeout = timeout_s or self.config.agent_timeout_s
        errors: list[str] = []
        attempts = 0
        retry_used = False
        timed_out = False
        for attempt in range(1, self.config.max_agent_attempts + 1):
            attempts = attempt
            try:
                raw = await asyncio.wait_for(self._invoke(agent, context), timeout=timeout)
                result = _normalise_result(raw, agent)
                return AgentExecution(replace(result, retries=max(result.retries, attempt - 1)), attempts, retry_used, timed_out)
            except asyncio.TimeoutError:
                timed_out = True
                error: BaseException = TimeoutError(f"{getattr(agent, 'name', 'agent')} exceeded {timeout:g}s timeout")
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                error = exc
            errors.append(f"{type(error).__name__}: {error}".strip())
            if not _retryable(error) or attempt >= self.config.max_agent_attempts:
                break
            retry_used = True
        result = AgentResult(
            ok=False,
            output={"reason": "agent_timeout" if timed_out else "agent_failed", "agent": getattr(agent, "name", agent.__class__.__name__)},
            errors=errors,
            retries=max(0, attempts - 1),
            fallback_used=True,
            model_name=str(getattr(agent, "model_name", "n/a")),
            provider=str(getattr(agent, "provider", "n/a")),
        )
        return AgentExecution(result, attempts, retry_used, timed_out)

    def _persist(self, agent: Agent, context: RunContext, execution: AgentExecution) -> AgentResult:
        result = execution.result
        try:
            persist_agent_run(agent, context, result, database_url=self.database_url)
        except Exception as exc:
            logger.exception("agent_run_persistence_failed trace_id=%s agent=%s", context.trace_id, getattr(agent, "name", "unknown"))
            result = replace(result, errors=[*result.errors, f"persistence: {type(exc).__name__}: {exc}"])
        return result

    def _persist_stage(
        self,
        name: str,
        context: RunContext,
        output: Mapping[str, Any],
        *,
        latency_ms: float,
        confidence: float = 1.0,
    ) -> AgentResult:
        """Persist deterministic FUSE/ROUTE work with measured latency."""

        result = AgentResult(
            ok=True,
            output=dict(output),
            confidence=confidence,
            latency_ms=max(0.0, latency_ms),
            model_name="deterministic",
            provider="local",
        )
        return self._persist(_StageAgent(name), context, AgentExecution(result, 1))

    @staticmethod
    def _apply_guardrail_draft(diagnosis: AgentResult, guardrail: AgentResult) -> AgentResult:
        """Use the exact draft accepted and normalized by the guardrail."""

        draft = _result_output(guardrail).get("draft")
        if not isinstance(draft, Mapping):
            return diagnosis
        return replace(diagnosis, output={**_result_output(diagnosis), **dict(draft)})

    async def _fanout(self, context: RunContext) -> tuple[AgentExecution, AgentExecution]:
        if self.config.sequential:
            sensor = await self._run_agent(self.sensor_agent, context)
            maintenance = await self._run_agent(self.maintenance_agent, context)
            return sensor, maintenance
        return await asyncio.gather(self._run_agent(self.sensor_agent, context), self._run_agent(self.maintenance_agent, context))

    def _coordinator_payload(
        self,
        base: Mapping[str, Any],
        sensor: AgentResult,
        maintenance: AgentResult,
        fusion_result: Mapping[str, Any],
        missing: Sequence[str],
    ) -> dict[str, Any]:
        sensor_output = _result_output(sensor)
        maintenance_output = _result_output(maintenance)
        evidence = _evidence_bundle(
            sensor_output,
            maintenance_output,
            fusion_result,
            sensor.evidence,
            maintenance.evidence,
        )
        payload = dict(base)
        payload.update(
            {
                "sensor_result": sensor_output,
                "history_result": maintenance_output,
                "fusion_result": dict(fusion_result),
                "machine_meta": dict(base.get("machine_meta", base.get("machine_context", {}))) if isinstance(base.get("machine_meta", base.get("machine_context", {})), Mapping) else {},
                "evidence_bundle": evidence,
                "missing_arms": list(missing),
                "missing_evidence": [f"{arm} arm is unavailable; do not treat it as supporting evidence." for arm in missing],
            }
        )
        return payload

    @staticmethod
    def _guardrail_input(diagnosis: AgentResult, evidence: Mapping[str, Any]) -> dict[str, Any]:
        output = _result_output(diagnosis)
        fields = ("fault_diagnosis", "reasoning_steps", "facts", "assumptions", "recommended_actions", "limits", "citations")
        return {"draft": {key: output[key] for key in fields if key in output}, "evidence_bundle": evidence}

    @staticmethod
    def _guardrail_accepted(result: AgentResult) -> bool:
        output = _result_output(result)
        return result.ok and bool(output.get("accepted", False))

    @classmethod
    def _stamp_guardrail_verdict(cls, result: AgentResult) -> AgentResult:
        output = _result_output(result)
        if not output:
            return result
        verdict = "accepted" if cls._guardrail_accepted(result) else "rejected"
        return replace(result, output={**output, "guardrail_verdict": verdict})

    @staticmethod
    def _rejection_reasons(result: AgentResult) -> list[str]:
        output = _result_output(result)
        rejections = output.get("rejections", [])
        reasons: list[str] = []
        if isinstance(rejections, Sequence) and not isinstance(rejections, (str, bytes)):
            for item in rejections:
                reasons.append(str(_field(item, "message", _field(item, "rule", item))))
        return reasons or list(result.errors) or ["Guardrail rejected the diagnosis draft."]

    def _template_result(self, context: RunContext, previous: AgentResult | None, reason: str) -> AgentResult:
        if isinstance(self.coordinator_agent, CoordinatorAgent):
            # The real Coordinator owns the deterministic template, so its
            # citation and fusion invariants remain identical to normal use.
            template_agent = CoordinatorAgent(llm_api_key="", llm_model_cfg=getattr(self.coordinator_agent, "llm_model_cfg", None))
            result = template_agent.run(context)
            return replace(result, fallback_used=True, errors=[*result.errors, reason], model_name="deterministic-template", provider="local")
        output = dict(_result_output(previous)) if previous is not None else {}
        output.update({"generated": False, "model": "deterministic-template", "provider": "local", "fallback_reason": reason})
        return AgentResult(
            ok=True,
            output=output,
            evidence=list(previous.evidence) if previous is not None else [],
            confidence=float(previous.confidence) if previous is not None else 0.0,
            tokens=previous.tokens if previous is not None else {},
            fallback_used=True,
            model_name="deterministic-template",
            provider="local",
            errors=[reason],
        )

    async def _execute(self, context: RunContext) -> DiagnosisRunResult:
        await self._emit(RunState.INIT)
        await self._emit(RunState.FANOUT, sequential=self.config.sequential)
        sensor_execution, maintenance_execution = await self._fanout(context)
        sensor = self._persist(self.sensor_agent, context, sensor_execution)
        maintenance = self._persist(self.maintenance_agent, context, maintenance_execution)
        missing = [name for name, result in (("sensor", sensor), ("maintenance_log", maintenance)) if not result.ok]

        await self._emit(RunState.FUSE, partial=bool(missing), missing_arms=missing)
        fusion_started = time.perf_counter()
        base_payload = context.payload
        sensor_output = _result_output(sensor)
        maintenance_output = _result_output(maintenance)
        sensor_evidence = _sensor_evidence(sensor_output, missing="sensor" in missing)
        history_evidence = _history_evidence(maintenance_output)
        machine_value = base_payload.get("machine_meta", base_payload.get("machine_context", {}))
        machine = MachineContext(
            criticality=_field(machine_value, "criticality", "minor"),
            hourly_downtime_cost_gbp=float(_field(machine_value, "hourly_downtime_cost_gbp", _field(machine_value, "downtime_cost_gbp", 0.0)) or 0.0),
        )
        decision = fuse(sensor_evidence, history_evidence, machine)
        fusion_result = _fusion_output(decision)
        fusion_context = RunContext(
            trace_id=self.trace_id,
            payload={
                "sensor_result": sensor_output,
                "history_result": maintenance_output,
                "machine_meta": _json_ready(machine_value),
                "missing_arms": list(missing),
            },
        )
        self._persist_stage(
            "fuse",
            fusion_context,
            fusion_result,
            latency_ms=(time.perf_counter() - fusion_started) * 1000,
            confidence=decision.confidence,
        )

        await self._emit(RunState.DIAGNOSE, missing_arms=missing)
        coordinator_context = RunContext(
            trace_id=self.trace_id,
            payload=self._coordinator_payload(base_payload, sensor, maintenance, fusion_result, missing),
        )
        diagnosis_execution = await self._run_agent(self.coordinator_agent, coordinator_context)
        diagnosis = self._persist(self.coordinator_agent, coordinator_context, diagnosis_execution)
        template_used = bool(diagnosis.fallback_used)

        evidence = coordinator_context.payload["evidence_bundle"]
        guardrail_results: list[AgentResult] = []
        await self._emit(RunState.REFLECT, attempt=1)
        guardrail_context = RunContext(
            trace_id=self.trace_id,
            payload=self._guardrail_input(diagnosis, evidence),
        )
        guardrail_execution = await self._run_agent(self.guardrail_agent, guardrail_context)
        guardrail_execution = replace(guardrail_execution, result=self._stamp_guardrail_verdict(guardrail_execution.result))
        guardrail_result = self._persist(self.guardrail_agent, guardrail_context, guardrail_execution)
        guardrail_results.append(guardrail_result)
        accepted = self._guardrail_accepted(guardrail_result)
        if accepted:
            diagnosis = self._apply_guardrail_draft(diagnosis, guardrail_result)
        retry_count = 0

        if not accepted and diagnosis_execution.transport_retry_used:
            diagnosis = self._template_result(coordinator_context, diagnosis, "transport retry consumed before guardrail rejection")
            template_used = True
            diagnosis = self._persist(self.coordinator_agent, coordinator_context, AgentExecution(diagnosis, 1, False, False))
        elif not accepted:
            retry_count = 1
            reasons = self._rejection_reasons(guardrail_result)
            await self._emit(RunState.DIAGNOSE, attempt=2, retry=True, reason="guardrail_rejection", rejection_reasons=reasons)
            retry_payload = dict(coordinator_context.payload)
            retry_payload["reflection_rejections"] = reasons
            retry_payload["previous_diagnosis"] = _result_output(diagnosis)
            retry_evidence = dict(retry_payload.get("evidence_bundle", {}))
            retry_items = list(retry_evidence.get("evidence", []))
            retry_items.extend(
                {"id": f"E-REFLECTION-{index}", "kind": "guardrail_feedback", "text": f"Guardrail feedback: {reason}"}
                for index, reason in enumerate(reasons, 1)
            )
            retry_evidence["evidence"] = retry_items
            retry_payload["evidence_bundle"] = retry_evidence
            retry_context = RunContext(trace_id=self.trace_id, payload=retry_payload)
            retry_execution = await self._run_agent(self.coordinator_agent, retry_context)
            diagnosis = self._persist(self.coordinator_agent, retry_context, retry_execution)
            await self._emit(RunState.REFLECT, attempt=2, retry=True)
            retry_guardrail_context = RunContext(trace_id=self.trace_id, payload=self._guardrail_input(diagnosis, evidence))
            retry_guardrail_execution = await self._run_agent(self.guardrail_agent, retry_guardrail_context)
            retry_guardrail_execution = replace(retry_guardrail_execution, result=self._stamp_guardrail_verdict(retry_guardrail_execution.result))
            retry_guardrail_result = self._persist(self.guardrail_agent, retry_guardrail_context, retry_guardrail_execution)
            guardrail_results.append(retry_guardrail_result)
            if self._guardrail_accepted(retry_guardrail_result):
                diagnosis = self._apply_guardrail_draft(diagnosis, retry_guardrail_result)
            else:
                diagnosis = self._template_result(retry_context, diagnosis, "second guardrail rejection")
                template_used = True
                diagnosis = self._persist(self.coordinator_agent, retry_context, AgentExecution(diagnosis, 1, False, False))

        if diagnosis.fallback_used:
            template_used = True
        await self._emit(RunState.ROUTE, autonomy=decision.autonomy)
        route_started = time.perf_counter()
        route = _route(fusion_result)
        route_context = RunContext(
            trace_id=self.trace_id,
            payload={"fusion_result": fusion_result, "diagnosis": _result_output(diagnosis)},
        )
        self._persist_stage(
            "route",
            route_context,
            route,
            latency_ms=(time.perf_counter() - route_started) * 1000,
            confidence=decision.confidence,
        )
        await self._emit(RunState.DONE, status="completed", route=route, template_used=template_used)
        return DiagnosisRunResult(
            trace_id=self.trace_id,
            status="completed",
            state=RunState.DONE.value,
            sensor_result=sensor,
            maintenance_result=maintenance,
            fusion_result=fusion_result,
            diagnosis_result=diagnosis,
            guardrail_results=guardrail_results,
            route=route,
            events=list(self.events),
            missing_arms=missing,
            retry_count=retry_count,
            template_used=template_used,
        )

    async def execute(self, ctx: RunContext | Mapping[str, Any] | None = None, **payload: Any) -> DiagnosisRunResult:
        """Run one diagnosis with a single trace ID and a hard wall-clock cap."""

        if ctx is not None and payload:
            raise ValueError("provide either ctx or keyword payload, not both")
        if isinstance(ctx, RunContext):
            context = ctx
        elif isinstance(ctx, Mapping):
            context = RunContext(payload=ctx)
        elif payload:
            context = RunContext(payload=payload)
        else:
            context = RunContext()
        self.trace_id = context.trace_id or uuid4().hex
        self.events = []
        self._event_sequence = 0
        try:
            return await asyncio.wait_for(self._execute(context), timeout=self.config.run_timeout_s)
        except asyncio.TimeoutError:
            await self._emit(RunState.DONE, status="failed", reason="run_timeout")
            failed = AgentResult(ok=False, output={"reason": "run_timeout"}, errors=[f"diagnosis exceeded {self.config.run_timeout_s:g}s"], fallback_used=True, model_name="n/a", provider="orchestrator")
            empty = AgentResult(ok=False, output={"reason": "not_completed"}, errors=["run did not reach this arm"], model_name="n/a", provider="orchestrator")
            return DiagnosisRunResult(
                trace_id=self.trace_id,
                status="failed",
                state=RunState.DONE.value,
                sensor_result=empty,
                maintenance_result=empty,
                fusion_result={},
                diagnosis_result=failed,
                events=list(self.events),
                errors=["run_timeout"],
                template_used=True,
            )

    async def run(self, ctx: RunContext | Mapping[str, Any] | None = None, **payload: Any) -> DiagnosisRunResult:
        """Async alias matching the terminology used by the other agents."""

        return await self.execute(ctx, **payload)


async def orchestrate(
    sensor_agent: Agent,
    maintenance_agent: Agent,
    coordinator_agent: Agent,
    guardrail_agent: Agent,
    ctx: RunContext | Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> DiagnosisRunResult:
    """Convenience function for one-shot callers and API adapters."""

    return await DiagnosisRun(
        sensor_agent,
        maintenance_agent,
        coordinator_agent,
        guardrail_agent,
        **kwargs,
    ).execute(ctx)


__all__ = [
    "AgentExecution",
    "DiagnosisRun",
    "DiagnosisRunResult",
    "OrchestratorConfig",
    "RunState",
    "orchestrate",
]
