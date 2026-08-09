from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.base import AgentResult, RetryConfig, RunContext, TransientAgentError, run_agent
from api.main import app
from core.db import session_scope
from core.models import AgentRun


class SuccessfulAgent:
    name = "fake"
    version = "v1"
    model_name = "n/a"
    provider = "n/a"

    def run(self, ctx: RunContext) -> AgentResult:
        return AgentResult(
            ok=True,
            output={"answer": "grounded"},
            evidence=[{"id": "M1"}],
            confidence=0.92,
            tokens={"prompt_tokens": 11, "completion_tokens": 7},
        )


class FlakyAgent:
    name = "flaky"
    version = "v2"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, _: RunContext) -> AgentResult:
        self.calls += 1
        if self.calls <= 2:
            raise TransientAgentError("temporary dependency failure")
        return AgentResult(ok=True, output={"answer": "recovered"})


class BrokenAgent:
    name = "broken"
    version = "v1"

    def run(self, _: RunContext) -> AgentResult:
        raise ValueError("unexpected agent failure")


def _retry_without_wait() -> RetryConfig:
    return RetryConfig(initial_delay_s=0.001, max_delay_s=0.001, jitter_s=0.0)


def test_fake_agent_persists_one_complete_trace_row(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    log_path = tmp_path / "logs" / "app.jsonl"
    trace_id = "trace-fake-001"

    result = run_agent(
        SuccessfulAgent(),
        RunContext(trace_id=trace_id, payload={"machine_id": "PUMP-07"}),
        database_url=database_url,
        log_path=log_path,
    )

    assert result.ok is True
    assert result.retries == 0
    with session_scope(database_url) as session:
        rows = session.query(AgentRun).filter_by(trace_id=trace_id).all()
        assert len(rows) == 1
        row = rows[0]
        fields = [
            row.trace_id, row.agent, row.version, row.input_hash, row.output, row.latency_ms,
            row.prompt_tokens, row.completion_tokens, row.cost_gbp, row.model_name, row.provider,
            row.retries, row.fallback_used, row.errors,
        ]
        assert all(value is not None for value in fields)
        assert row.output["answer"] == "grounded"
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["agent_started", "agent_attempt", "agent_completed"]
    assert all(line["trace_id"] == trace_id for line in lines)
    assert f'"trace_id":"{trace_id}"' in log_path.read_text(encoding="utf-8")


def test_retry_budget_records_two_retries_after_two_transient_failures(tmp_path: Path) -> None:
    agent = FlakyAgent()
    result = run_agent(
        agent,
        RunContext(trace_id="trace-retry-001"),
        database_url=f"sqlite:///{tmp_path / 'plant.db'}",
        retry_config=_retry_without_wait(),
        log_path=tmp_path / "app.jsonl",
    )

    assert agent.calls == 3
    assert result.ok is True
    assert result.retries == 2


def test_unexpected_exception_is_returned_and_persisted(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    result = run_agent(
        BrokenAgent(),
        RunContext(trace_id="trace-error-001"),
        database_url=database_url,
        log_path=tmp_path / "app.jsonl",
    )

    assert result.ok is False
    assert result.errors == ["ValueError: unexpected agent failure"]
    with session_scope(database_url) as session:
        row = session.query(AgentRun).filter_by(trace_id="trace-error-001").one()
        assert row.errors == result.errors
        assert row.output["_agent_result"]["ok"] is False


def test_runs_endpoint_returns_persisted_trace_in_insert_order(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    trace_id = "trace-api-001"
    run_agent(
        SuccessfulAgent(),
        RunContext(trace_id=trace_id),
        database_url=database_url,
        log_path=tmp_path / "app.jsonl",
    )
    second = SuccessfulAgent()
    second.name = "second"  # type: ignore[attr-defined]
    run_agent(
        second,
        RunContext(trace_id=trace_id),
        database_url=database_url,
        log_path=tmp_path / "app.jsonl",
    )

    import core.db

    monkeypatch.setattr(core.db.settings, "db_url", database_url)
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200
    response = client.get(f"/runs/{trace_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == trace_id
    assert [item["agent"] for item in payload["runs"]] == ["fake", "second"]
    assert payload["runs"][0]["state"] == "completed"
