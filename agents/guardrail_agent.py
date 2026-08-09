"""Agent adapter for the deterministic guardrail engine."""

from __future__ import annotations

from typing import Any

from agents.base import AgentResult, RunContext
from core.guardrails import run_guardrails


class GuardrailAgent:
    """Run E-01 checks without making an LLM call or exposing tools."""

    name = "guardrail"
    version = "v1"
    model_name = "n/a"
    provider = "deterministic"

    def __init__(self, *, database_url: str | None = None, db_url: str | None = None) -> None:
        if database_url and db_url and database_url != db_url:
            raise ValueError("database_url and db_url must refer to the same database")
        self.database_url = database_url or db_url

    def run(self, ctx: RunContext) -> AgentResult:
        payload: dict[str, Any] = dict(ctx.payload)
        draft = payload.get("draft", payload)
        evidence = payload.get("evidence_bundle", payload.get("evidence", {}))
        result = run_guardrails(draft, evidence, trace_id=ctx.trace_id, database_url=self.database_url)
        return AgentResult(
            ok=True,
            output=result.as_dict(),
            evidence=[item.as_dict() for item in result.rejections],
            confidence=1.0 if result.accepted else 0.0,
            model_name=self.model_name,
            provider=self.provider,
        )


__all__ = ["GuardrailAgent"]
