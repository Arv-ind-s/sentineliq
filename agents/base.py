"""Shared agent contract, retry policy, trace emission, and run persistence.

Agents are deliberately small objects that implement :class:`Agent`.  The
runner in this module owns the operational concerns that must be identical for
every agent: bounded transient retries, a diagnosis trace ID, structured trace
events, and a durable ``agent_runs`` record even when the agent raises.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any, ParamSpec, Protocol, TypeVar
from uuid import uuid4

from sqlalchemy import select
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal installs
    class _FallbackLogger:
        def bind(self, **_: Any) -> "_FallbackLogger":
            return self

        def info(self, event: str, **fields: Any) -> None:
            logger.info("%s %s", event, fields)

    class _FallbackContextVars:
        @staticmethod
        @contextmanager
        def bound_contextvars(**_: Any) -> Iterator[None]:
            yield

    class _FallbackStructlog:
        contextvars = _FallbackContextVars()

        @staticmethod
        def get_logger(_: str) -> _FallbackLogger:
            return _FallbackLogger()

    structlog = _FallbackStructlog()

from core.config import ROOT
from core.db import create_schema, session_scope
from core.models import AgentRun


logger = logging.getLogger(__name__)
DEFAULT_LOG_PATH = ROOT / "logs" / "app.jsonl"
_TRACE_LOG_LOCK = Lock()


class TransientAgentError(RuntimeError):
    """Base exception for failures that are safe for the shared retry policy."""


@dataclass(frozen=True)
class RetryConfig:
    """Per-agent retry settings.

    ``max_attempts`` includes the first call.  Therefore the default permits
    two retries, which is the retry budget exposed in ``AgentResult``.
    """

    max_attempts: int = 3
    initial_delay_s: float = 0.5
    max_delay_s: float = 2.0
    jitter_s: float = 0.1
    retry_exceptions: tuple[type[BaseException], ...] = (
        TransientAgentError,
        TimeoutError,
        ConnectionError,
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay_s < 0 or self.max_delay_s < self.initial_delay_s or self.jitter_s < 0:
            raise ValueError("retry delays must be non-negative and ordered")


@dataclass(frozen=True)
class TokenUsage:
    """Prompt and completion token counts attached to an agent result."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, init=False)
class RunContext:
    """Immutable hand-off context shared by every agent in one diagnosis."""

    trace_id: str
    payload: Mapping[str, Any]

    def __init__(
        self,
        trace_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        *,
        input: Mapping[str, Any] | None = None,
    ) -> None:
        if payload is not None and input is not None:
            raise ValueError("provide either payload or input, not both")
        object.__setattr__(self, "trace_id", trace_id or uuid4().hex)
        object.__setattr__(self, "payload", dict(payload if payload is not None else input or {}))

    @property
    def input(self) -> Mapping[str, Any]:
        """Compatibility alias for callers that name the hand-off ``input``."""

        return self.payload

    def as_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "payload": _json_ready(self.payload)}


@dataclass
class AgentResult:
    """Stable result envelope returned by every agent invocation."""

    ok: bool
    output: Any = field(default_factory=dict)
    evidence: list[Any] = field(default_factory=list)
    confidence: float = 0.0
    tokens: TokenUsage | Mapping[str, Any] | int = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    retries: int = 0
    fallback_used: bool = False
    model_name: str = "n/a"
    provider: str = "n/a"
    cost_gbp: float = 0.0

    def token_usage(self) -> TokenUsage:
        return _token_usage(self.tokens)

    def as_dict(self) -> dict[str, Any]:
        usage = self.token_usage()
        return {
            "ok": self.ok,
            "output": _json_ready(self.output),
            "evidence": _json_ready(self.evidence),
            "confidence": float(self.confidence),
            "tokens": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            },
            "latency_ms": float(self.latency_ms),
            "errors": list(self.errors),
            "retries": int(self.retries),
            "fallback_used": bool(self.fallback_used),
            "model_name": self.model_name,
            "provider": self.provider,
        }


class Agent(Protocol):
    """Protocol implemented by sensor, history, coordinator, and support agents."""

    name: str
    version: str

    def run(self, ctx: RunContext) -> AgentResult: ...


@dataclass(frozen=True)
class PersistedAgentRun:
    """Detached representation of an ``agent_runs`` row."""

    id: int
    created_at: int
    trace_id: str
    agent: str
    version: str
    input_hash: str
    output: dict[str, Any]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_gbp: float
    model_name: str
    provider: str
    retries: int
    fallback_used: bool
    errors: list[str]


P = ParamSpec("P")
R = TypeVar("R")


def retry_with_backoff(config: RetryConfig | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate a callable with tenacity's bounded transient retry policy."""

    retry_config = config or RetryConfig()

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            retrying = Retrying(
                stop=stop_after_attempt(retry_config.max_attempts),
                wait=wait_exponential_jitter(
                    initial=retry_config.initial_delay_s,
                    max=retry_config.max_delay_s,
                    jitter=retry_config.jitter_s,
                ),
                retry=retry_if_exception_type(retry_config.retry_exceptions),
                reraise=True,
            )
            return retrying(function, *args, **kwargs)

        return wrapped

    return decorator


@contextmanager
def trace_context(trace_id: str, agent: str | None = None) -> Iterator[None]:
    """Bind a trace ID to structlog context for the duration of a call."""

    values: dict[str, Any] = {"trace_id": trace_id}
    if agent:
        values["agent"] = agent
    with structlog.contextvars.bound_contextvars(**values):
        yield


def _json_ready(value: Any) -> Any:
    """Convert result values to strict JSON-compatible primitives."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(child) for child in value]
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _token_usage(tokens: TokenUsage | Mapping[str, Any] | int | Any) -> TokenUsage:
    if isinstance(tokens, TokenUsage):
        return tokens
    if isinstance(tokens, Mapping):
        prompt = tokens.get("prompt_tokens", tokens.get("prompt", 0))
        completion = tokens.get("completion_tokens", tokens.get("completion", 0))
        return TokenUsage(prompt_tokens=max(0, int(prompt or 0)), completion_tokens=max(0, int(completion or 0)))
    if isinstance(tokens, (int, float)):
        return TokenUsage(completion_tokens=max(0, int(tokens)))
    return TokenUsage()


def _normalise_result(value: AgentResult | Mapping[str, Any]) -> AgentResult:
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
            errors=[str(error) for error in value.get("errors", [])],
            retries=int(value.get("retries", 0)),
            fallback_used=bool(value.get("fallback_used", False)),
            model_name=str(value.get("model_name", "n/a")),
            provider=str(value.get("provider", "n/a")),
            cost_gbp=float(value.get("cost_gbp", 0.0)),
        )
    raise TypeError("agent.run() must return AgentResult or a result mapping")


def _context(value: RunContext | Mapping[str, Any] | None) -> RunContext:
    if value is None:
        return RunContext()
    if isinstance(value, RunContext):
        return value
    return RunContext(payload=value)


def _input_hash(ctx: RunContext) -> str:
    canonical = json.dumps(_json_ready(ctx.payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_trace_event(log_path: str | Path, *, trace_id: str, event: str, agent: str, **fields: Any) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"trace_id": trace_id, "event": event, "agent": agent, **fields}
    line = json.dumps(_json_ready(record), sort_keys=False, separators=(",", ":"))
    with _TRACE_LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _persisted_output(result: AgentResult, ctx: RunContext) -> dict[str, Any]:
    output = _json_ready(result.output)
    if isinstance(output, dict):
        envelope = dict(output)
    else:
        envelope = {"value": output}
    # The replay utility needs the exact context that reached each agent.  It
    # is part of the stored trace contract and is deliberately JSON-normalised
    # just like the result, so old callers do not need a custom serializer.
    envelope["_run_input"] = _json_ready(ctx.payload)
    envelope["_agent_result"] = {
        "ok": result.ok,
        "evidence": _json_ready(result.evidence),
        "confidence": float(result.confidence),
        "errors": list(result.errors),
    }
    return envelope


def persist_agent_run(
    agent: Agent,
    ctx: RunContext,
    result: AgentResult,
    *,
    database_url: str | None = None,
) -> PersistedAgentRun:
    """Write one complete agent run and return a detached row representation."""

    create_schema(database_url)
    usage = result.token_usage()
    row = AgentRun(
        trace_id=ctx.trace_id,
        agent=str(getattr(agent, "name", agent.__class__.__name__)),
        version=str(getattr(agent, "version", "1")),
        input_hash=_input_hash(ctx),
        output=_persisted_output(result, ctx),
        latency_ms=max(0, int(round(result.latency_ms))),
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_gbp=max(0.0, float(result.cost_gbp)),
        model_name=str(result.model_name or getattr(agent, "model_name", "n/a")),
        provider=str(result.provider or getattr(agent, "provider", "n/a")),
        retries=max(0, int(result.retries)),
        fallback_used=bool(result.fallback_used),
        errors=[str(error) for error in result.errors],
    )
    with session_scope(database_url) as session:
        session.add(row)
        session.flush()
        return PersistedAgentRun(
            id=int(row.id),
            created_at=int(row.created_at),
            trace_id=row.trace_id,
            agent=row.agent,
            version=row.version,
            input_hash=row.input_hash,
            output=dict(row.output),
            latency_ms=row.latency_ms,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            cost_gbp=row.cost_gbp,
            model_name=row.model_name,
            provider=row.provider,
            retries=row.retries,
            fallback_used=row.fallback_used,
            errors=list(row.errors),
        )


def load_agent_runs(trace_id: str, *, database_url: str | None = None) -> list[PersistedAgentRun]:
    """Load one diagnosis trace in insertion order."""

    with session_scope(database_url) as session:
        rows = session.scalars(select(AgentRun).where(AgentRun.trace_id == trace_id).order_by(AgentRun.id)).all()
        return [
            PersistedAgentRun(
                id=int(row.id),
                created_at=int(row.created_at),
                trace_id=row.trace_id,
                agent=row.agent,
                version=row.version,
                input_hash=row.input_hash,
                output=dict(row.output),
                latency_ms=row.latency_ms,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                cost_gbp=row.cost_gbp,
                model_name=row.model_name,
                provider=row.provider,
                retries=row.retries,
                fallback_used=row.fallback_used,
                errors=list(row.errors),
            )
            for row in rows
        ]


def run_agent(
    agent: Agent,
    ctx: RunContext | Mapping[str, Any] | None = None,
    *,
    database_url: str | None = None,
    retry_config: RetryConfig | None = None,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> AgentResult:
    """Execute, trace, retry, and persist one agent invocation.

    Unexpected exceptions are converted to a failed result after persistence;
    they never escape as a lost run.  Only the exception types in
    :class:`RetryConfig` consume retry budget.
    """

    run_context = _context(ctx)
    agent_name = str(getattr(agent, "name", agent.__class__.__name__))
    policy = retry_config or getattr(agent, "retry_config", RetryConfig())
    started = time.perf_counter()
    attempts = 0
    _write_trace_event(log_path, trace_id=run_context.trace_id, event="agent_started", agent=agent_name)
    bound_logger = structlog.get_logger("sentinel.agent").bind(trace_id=run_context.trace_id, agent=agent_name)

    def before_attempt(state: Any) -> None:
        nonlocal attempts
        attempts = int(state.attempt_number)
        _write_trace_event(
            log_path,
            trace_id=run_context.trace_id,
            event="agent_attempt",
            agent=agent_name,
            attempt=attempts,
        )
        bound_logger.info("agent_attempt", attempt=attempts)

    try:
        with trace_context(run_context.trace_id, agent_name):
            retrying = Retrying(
                stop=stop_after_attempt(policy.max_attempts),
                wait=wait_exponential_jitter(
                    initial=policy.initial_delay_s,
                    max=policy.max_delay_s,
                    jitter=policy.jitter_s,
                ),
                retry=retry_if_exception_type(policy.retry_exceptions),
                reraise=True,
                before=before_attempt,
            )
            raw_result = retrying(agent.run, run_context)
        result = _normalise_result(raw_result)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}".strip()
        result = AgentResult(ok=False, errors=[error])

    elapsed_ms = (time.perf_counter() - started) * 1000
    agent_model = str(getattr(agent, "model_name", "n/a"))
    agent_provider = str(getattr(agent, "provider", "n/a"))
    result = replace(
        result,
        latency_ms=elapsed_ms,
        retries=max(int(result.retries), max(0, attempts - 1)),
        model_name=result.model_name if result.model_name != "n/a" else agent_model,
        provider=result.provider if result.provider != "n/a" else agent_provider,
    )
    persist_agent_run(agent, run_context, result, database_url=database_url)
    _write_trace_event(
        log_path,
        trace_id=run_context.trace_id,
        event="agent_completed",
        agent=agent_name,
        ok=result.ok,
        latency_ms=round(result.latency_ms, 3),
        retries=result.retries,
        fallback_used=result.fallback_used,
        errors=result.errors,
    )
    bound_logger.info("agent_completed", ok=result.ok, retries=result.retries, latency_ms=round(result.latency_ms, 3))
    return result


class AgentRunner:
    """Reusable dependency-injection seam for orchestrators and tests."""

    def __init__(self, *, database_url: str | None = None, log_path: str | Path = DEFAULT_LOG_PATH) -> None:
        self.database_url = database_url
        self.log_path = log_path

    def run(self, agent: Agent, ctx: RunContext | Mapping[str, Any] | None = None, *, retry_config: RetryConfig | None = None) -> AgentResult:
        return run_agent(
            agent,
            ctx,
            database_url=self.database_url,
            retry_config=retry_config,
            log_path=self.log_path,
        )


# Explicit aliases make the runner easy to discover without creating multiple
# implementations of the persistence path.
execute_agent = run_agent
retry = retry_with_backoff


__all__ = [
    "Agent",
    "AgentResult",
    "AgentRunner",
    "PersistedAgentRun",
    "RetryConfig",
    "RunContext",
    "TokenUsage",
    "TransientAgentError",
    "execute_agent",
    "load_agent_runs",
    "persist_agent_run",
    "retry",
    "retry_with_backoff",
    "run_agent",
    "trace_context",
]
