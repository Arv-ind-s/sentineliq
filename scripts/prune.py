"""Prune expiring operational data while preserving the audit ledger.

The plant database and LLM cache are separate stores.  This utility handles
both in one run, uses an explicit cutoff, and never issues a write against the
``audit_ledger`` table.  ``--dry-run`` is available for change review before a
retention job is scheduled.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from core.db import create_schema, session_scope
from core.models import AgentRun, AuditLedger
from core.runtime_paths import runtime_paths


RETENTION_DAYS = 30
SECONDS_PER_DAY = 24 * 60 * 60
DEFAULT_CACHE_PATH = runtime_paths.llm_cache_path


def _count_cache_rows(connection: sqlite3.Connection, table: str, cutoff_s: float) -> int:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if exists is None:
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE created_at < ?", (cutoff_s,)).fetchone()[0])


def _prune_cache(path: Path, cutoff_s: float, *, dry_run: bool) -> dict[str, int]:
    if not path.exists():
        return {"llm_cache": 0, "llm_runs": 0}
    with closing(sqlite3.connect(path, timeout=5)) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        counts = {table: _count_cache_rows(connection, table, cutoff_s) for table in ("llm_cache", "llm_runs")}
        if not dry_run:
            for table in counts:
                if counts[table]:
                    connection.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff_s,))
            connection.commit()
        return counts


def prune(
    *,
    database_url: str | None = None,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    now_s: float | None = None,
    retention_days: int = RETENTION_DAYS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Prune rows older than ``retention_days`` and return an auditable summary."""

    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    current_s = float(now_s if now_s is not None else time.time())
    cutoff_s = current_s - retention_days * SECONDS_PER_DAY
    cutoff_ms = int(cutoff_s * 1000)
    database_url = database_url or settings.db_url
    create_schema(database_url)

    with session_scope(database_url) as session:
        agent_runs = int(session.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.created_at < cutoff_ms)) or 0)
        audit_before = int(session.scalar(select(func.count()).select_from(AuditLedger)) or 0)
        if not dry_run and agent_runs:
            session.execute(delete(AgentRun).where(AgentRun.created_at < cutoff_ms))

    cache_counts = _prune_cache(Path(cache_path), cutoff_s, dry_run=dry_run)
    with session_scope(database_url) as session:
        audit_after = int(session.scalar(select(func.count()).select_from(AuditLedger)) or 0)
    if audit_before != audit_after:
        raise RuntimeError("retention operation changed audit_ledger; refusing to report success")

    return {
        "cutoff": cutoff_s,
        "retention_days": retention_days,
        "dry_run": dry_run,
        "agent_runs": 0 if dry_run else agent_runs,
        "agent_runs_eligible": agent_runs,
        "llm_cache": 0 if dry_run else cache_counts["llm_cache"],
        "llm_cache_eligible": cache_counts["llm_cache"],
        "llm_runs": 0 if dry_run else cache_counts["llm_runs"],
        "llm_runs_eligible": cache_counts["llm_runs"],
        "audit_entries": audit_after,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None, help="SQLAlchemy plant DB URL; defaults to DB_URL")
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH, help="LLM cache SQLite path")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true", help="Report eligible rows without deleting them")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        result = prune(**vars(_parser().parse_args(argv)))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"prune failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
