import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agents.base import RunContext, run_agent
from agents.guardrail_agent import GuardrailAgent
from core.audit import append_event
from core.db import create_schema, session_scope
from core.models import AgentRun
from llm.cache import CachedResponse, LLMCache
from scripts.backup import backup
from scripts.prune import prune
from scripts.replay import replay_trace


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def test_replay_trace_reruns_current_guardrail_and_reports_field_diffs(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "plant.db")
    run_agent(
        GuardrailAgent(database_url=database_url),
        RunContext(trace_id="trace-a12", payload={"draft": {}, "evidence_bundle": {}}),
        database_url=database_url,
        log_path=tmp_path / "trace.jsonl",
    )

    assert replay_trace("trace-a12", database_url=database_url) == []

    with session_scope(database_url) as session:
        row = session.query(AgentRun).filter_by(trace_id="trace-a12").one()
        row.output = {**row.output, "accepted": True}

    differences = replay_trace("trace-a12", database_url=database_url)
    assert any(item.agent == "guardrail" and item.path == "$.accepted" for item in differences)


def test_prune_removes_old_agent_and_llm_rows_but_never_audit_rows(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "plant.db")
    create_schema(database_url)
    now = 2_000_000_000.0
    old_ms = int((now - 31 * 24 * 60 * 60) * 1000)
    new_ms = int((now - 2 * 24 * 60 * 60) * 1000)
    with session_scope(database_url) as session:
        session.add_all([
            AgentRun(trace_id="old", agent="test", version="v1", input_hash="a", output={}, latency_ms=1, prompt_tokens=0, completion_tokens=0, model_name="test", provider="local", created_at=old_ms),
            AgentRun(trace_id="new", agent="test", version="v1", input_hash="b", output={}, latency_ms=1, prompt_tokens=0, completion_tokens=0, model_name="test", provider="local", created_at=new_ms),
        ])
    append_event(actor="test", action="approval", subject="approval-1", payload={}, database_url=database_url, timestamp_ms=old_ms)

    cache_path = tmp_path / "llm_cache.db"
    with LLMCache(cache_path, ttl_s=900, clock=lambda: now) as cache:
        cache.put("old", CachedResponse("old", 1, 1, "model", "local"))
        cache.put("new", CachedResponse("new", 1, 1, "model", "local"))
        cache.record_run(prompt_tokens=1, completion_tokens=1, model="model", provider="local", latency_ms=1, cached=False, success=True)
    with sqlite3.connect(cache_path) as connection:
        connection.execute("UPDATE llm_cache SET created_at = ?, expires_at = ? WHERE cache_key = 'old'", (now - 31 * 24 * 60 * 60, now - 30 * 24 * 60 * 60))
        connection.execute("UPDATE llm_runs SET created_at = ?", (now - 31 * 24 * 60 * 60,))

    result = prune(database_url=database_url, cache_path=cache_path, now_s=now)
    assert result["agent_runs"] == 1
    assert result["llm_cache"] == 1
    assert result["llm_runs"] == 1
    with session_scope(database_url) as session:
        assert session.query(AgentRun).filter_by(trace_id="old").count() == 0
        assert session.query(AgentRun).filter_by(trace_id="new").count() == 1
        assert session.query(AgentRun).count() == 1
    with sqlite3.connect(cache_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM llm_runs").fetchone()[0] == 0

    # The audit ledger is in the plant DB, not the cache; it must remain intact.
    with sqlite3.connect(tmp_path / "plant.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0] == 1


def test_backup_contains_timestamped_approvals_and_audit_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "plant.db"
    database_url = _database_url(database_path)
    create_schema(database_url)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("INSERT INTO approvals (prediction_id, autonomy_level, required_role, state) VALUES ('pred-1', 'A0', 'planner', 'pending')")
    append_event(actor="planner", action="approval", subject="approval-1", payload={"state": "pending"}, database_url=database_url)

    result = backup(database_url=database_url, output_dir=tmp_path / "backups", now=datetime(2026, 8, 7, 12, 0, tzinfo=UTC))
    backup_path = Path(result["path"])
    assert backup_path.name == "sentinel_backup_20260807T120000Z.sqlite"
    assert backup_path.exists()
    assert result["tables"] == {"approvals": 1, "audit_ledger": 1}
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0] == 1
