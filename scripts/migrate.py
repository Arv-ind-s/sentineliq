"""Transactionally upgrade an existing Sentinel SQLite database.

The command is deliberately explicit: it creates a byte-for-byte backup,
applies the current schema migration in one transaction, and verifies foreign
keys plus the append-only audit chain before reporting success.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audit import ZERO_HASH, entry_hash
from core.config import ROOT, settings
from core.db import CURRENT_SCHEMA_VERSION, dispose_engine


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or ":memory:" in database_url:
        raise ValueError("the explicit migration command currently supports file-backed SQLite databases only")
    raw = database_url.removeprefix(prefix)
    return Path(raw) if raw.startswith("/") else ROOT / raw


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _add_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split(maxsplit=1)[0]
    if name not in _columns(connection, table):
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def _append_migration_audit(connection: sqlite3.Connection, approval_ids: list[int], timestamp_ms: int) -> None:
    if not approval_ids or "audit_ledger" not in {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }:
        return
    row = connection.execute("SELECT hash FROM audit_ledger ORDER BY seq DESC LIMIT 1").fetchone()
    previous = str(row[0]) if row else ZERO_HASH
    for approval_id in approval_ids:
        actor = "schema-migration"
        action = "approval_superseded"
        subject = f"approval-{approval_id}"
        payload = {"reason": "legacy pending approval had no immutable reviewed payload", "schema_version": CURRENT_SCHEMA_VERSION}
        current = entry_hash(
            prev_hash=previous,
            ts=timestamp_ms,
            actor=actor,
            action=action,
            subject=subject,
            payload=payload,
        )
        connection.execute(
            """INSERT INTO audit_ledger
               (ts, actor, action, subject, payload_json, prev_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp_ms, actor, action, subject, json.dumps(payload, sort_keys=True), previous, current),
        )
        previous = current


def _verify_audit_connection(connection: sqlite3.Connection) -> int:
    """Verify the audit chain using rows visible inside the migration transaction."""

    rows = connection.execute(
        "SELECT seq, ts, actor, action, subject, payload_json, prev_hash, hash FROM audit_ledger ORDER BY seq"
    ).fetchall()
    previous = ZERO_HASH
    expected_seq = 1
    for row in rows:
        sequence = int(row[0])
        if sequence != expected_seq:
            raise RuntimeError(f"audit verification failed before commit at sequence {expected_seq}")
        try:
            raw_payload = json.loads(row[5]) if isinstance(row[5], str) else row[5]
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"audit payload is invalid JSON at sequence {sequence}") from exc
        payload = raw_payload if isinstance(raw_payload, dict) else {"value": raw_payload}
        expected_hash = entry_hash(
            prev_hash=previous,
            ts=int(row[1]),
            actor=str(row[2]),
            action=str(row[3]),
            subject=str(row[4]),
            payload=payload,
        )
        if str(row[6]) != previous or str(row[7]) != expected_hash:
            raise RuntimeError(f"audit verification failed before commit at sequence {sequence}")
        previous = str(row[7])
        expected_seq += 1
    return len(rows)


def _materialize_missing_prediction_references(connection: sqlite3.Connection, timestamp_ms: int) -> int:
    """Repair legacy approval FKs without turning synthetic IDs into ledger signals."""

    missing_ids = [
        str(row[0])
        for row in connection.execute(
            """SELECT DISTINCT a.prediction_id
               FROM approvals AS a
               LEFT JOIN predictions AS p ON p.id = a.prediction_id
               WHERE p.id IS NULL
               ORDER BY a.prediction_id"""
        )
    ]
    if not missing_ids:
        return 0
    machine_ids = [str(row[0]) for row in connection.execute("SELECT id FROM machines ORDER BY id")]
    if not machine_ids:
        raise RuntimeError("cannot repair legacy prediction references without a machine row")
    for index, prediction_id in enumerate(missing_ids):
        lowered = prediction_id.lower()
        machine_id = next(
            (candidate for candidate in sorted(machine_ids, key=len, reverse=True) if lowered.startswith(f"pred-{candidate.lower()}-")),
            machine_ids[0],
        )
        connection.execute(
            """INSERT INTO predictions
               (id, machine_id, ts, p_failure, rul_p10, rul_p50, rul_p90,
                mode_probs, risk, band, confidence, predictor, model_version, source)
               VALUES (?, ?, ?, 0, 0, 0, 0, ?, 0, 'LOW', 0,
                       'legacy_reference', 'legacy-unscored', 'legacy_reference')""",
            (prediction_id, machine_id, timestamp_ms + index, json.dumps({"unknown": 1.0})),
        )
    return len(missing_ids)


def _migrate_v2(connection: sqlite3.Connection) -> dict[str, int]:
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "machines" not in tables:
        raise RuntimeError("database does not contain a Sentinel plant schema")
    now_ms = int(time.time() * 1000)

    if "agent_runs" in tables:
        _add_column(connection, "agent_runs", "created_at BIGINT")
        connection.execute("UPDATE agent_runs SET created_at = ? WHERE created_at IS NULL", (now_ms,))

    _add_column(connection, "predictions", "source VARCHAR(32) NOT NULL DEFAULT 'serving'")
    repaired_predictions = _materialize_missing_prediction_references(connection, now_ms)
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_window_model ON predictions(machine_id, ts, model_version)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_predictions_source_ts ON predictions(source, ts)")

    for definition in (
        "machine_id VARCHAR(32)",
        "trace_id VARCHAR(64)",
        "response_json JSON",
    ):
        _add_column(connection, "diagnoses", definition)
    connection.execute(
        """UPDATE diagnoses
           SET machine_id = (SELECT machine_id FROM predictions WHERE predictions.id = diagnoses.prediction_id)
           WHERE machine_id IS NULL"""
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_diagnoses_trace_id ON diagnoses(trace_id)")

    for definition in (
        "request_key VARCHAR(128)",
        "action_payload JSON",
        "payload_hash VARCHAR(64)",
        "erp_sync_status VARCHAR(24) NOT NULL DEFAULT 'not_requested'",
        "erp_workorder_id VARCHAR(64)",
    ):
        _add_column(connection, "approvals", definition)
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_approvals_request_key ON approvals(request_key)")
    pending = [
        int(row[0])
        for row in connection.execute(
            "SELECT id FROM approvals WHERE state = 'pending' AND action_payload IS NULL ORDER BY id"
        )
    ]
    if pending:
        connection.execute(
            """UPDATE approvals
               SET state = 'superseded',
                   reason = COALESCE(reason, 'Legacy pending approval superseded during safe payload migration'),
                   decided_at = ?
               WHERE state = 'pending' AND action_payload IS NULL""",
            (now_ms,),
        )
    _append_migration_audit(connection, pending, now_ms)

    evidence_count = int(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])
    evidence_columns = _columns(connection, "evidence")
    if "citation_id" not in evidence_columns:
        connection.execute(
            """CREATE TABLE evidence_v2 (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   prediction_id VARCHAR(64) NOT NULL REFERENCES predictions(id),
                   citation_id VARCHAR(16) NOT NULL,
                   kind VARCHAR(24) NOT NULL,
                   payload JSON NOT NULL,
                   CONSTRAINT uq_evidence_prediction_citation UNIQUE (prediction_id, citation_id)
               )"""
        )
        connection.execute(
            """INSERT INTO evidence_v2 (prediction_id, citation_id, kind, payload)
               SELECT prediction_id, id, kind, payload FROM evidence"""
        )
        connection.execute("DROP TABLE evidence")
        connection.execute("ALTER TABLE evidence_v2 RENAME TO evidence")

    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_version (
               id INTEGER PRIMARY KEY,
               version INTEGER NOT NULL,
               applied_at BIGINT NOT NULL
           )"""
    )
    connection.execute(
        """INSERT INTO schema_version (id, version, applied_at) VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET version = excluded.version, applied_at = excluded.applied_at""",
        (CURRENT_SCHEMA_VERSION, now_ms),
    )
    return {
        "evidence": evidence_count,
        "superseded_approvals": len(pending),
        "legacy_prediction_references_repaired": repaired_predictions,
    }


def migrate(database_url: str, backup_dir: Path | None = None) -> dict[str, object]:
    database_path = _sqlite_path(database_url).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target_dir = (backup_dir or database_path.parent / "backups").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_path = target_dir / f"{database_path.stem}-pre-v{CURRENT_SCHEMA_VERSION}-{timestamp}{database_path.suffix}"
    # SQLite's online backup API includes committed WAL pages and produces a
    # transactionally consistent copy without stopping readers.
    with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)

    dispose_engine(database_url)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        before = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("machines", "predictions", "diagnoses", "approvals", "evidence", "audit_ledger")
        }
        details = _migrate_v2(connection)
        after = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in before
        }
        for table in ("machines", "diagnoses", "evidence"):
            if before[table] != after[table]:
                raise RuntimeError(f"migration changed {table} row count: {before[table]} -> {after[table]}")
        expected_predictions = before["predictions"] + int(details["legacy_prediction_references_repaired"])
        if after["predictions"] != expected_predictions:
            raise RuntimeError(f"migration changed prediction row count unexpectedly: {before['predictions']} -> {after['predictions']}")
        if after["approvals"] != before["approvals"]:
            raise RuntimeError("migration changed approval row count")
        foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_errors:
            raise RuntimeError(f"foreign-key verification failed: {foreign_key_errors[:3]}")
        audit_entries = _verify_audit_connection(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    from core.audit import verify_ledger

    audit = verify_ledger(database_url=database_url)
    if not audit.intact:
        raise RuntimeError(f"audit verification failed after migration at sequence {audit.first_invalid_seq}")
    return {
        "database": str(database_path),
        "backup": str(backup_path),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "rows": after,
        "audit_entries": audit_entries,
        **details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.db_url)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--backfill-ledger", action="store_true", help="score historical telemetry after the schema upgrade")
    args = parser.parse_args()
    report = migrate(args.database_url, args.backup_dir)
    if args.backfill_ledger:
        from scripts.backfill_predictions import backfill, seed_demo_approval

        report["ledger_backfill"] = backfill(args.database_url)
        report["demo_approval"] = seed_demo_approval(args.database_url)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
