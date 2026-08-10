"""Back up the non-regenerable approvals and audit ledger tables."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
from core.runtime_paths import runtime_paths


TABLES = ("approvals", "audit_ledger")


def _sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("backup currently requires a SQLite DB_URL")
    value = database_url.removeprefix("sqlite:///")
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def _copy_table(source: sqlite3.Connection, destination: sqlite3.Connection, table: str) -> int:
    schema_row = source.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if schema_row is None or not schema_row[0]:
        raise ValueError(f"source database is missing required table: {table}")
    destination.execute(str(schema_row[0]))
    columns = [str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()]
    quoted_columns = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = source.execute(f'SELECT {quoted_columns} FROM "{table}"').fetchall()
    destination.executemany(f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})', rows)
    return len(rows)


def backup(
    *,
    database_url: str | None = None,
    output_dir: str | Path = runtime_paths.backup_root,
    output_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write an atomic SQLite backup containing exactly the two protected tables."""

    source_path = _sqlite_path(database_url or settings.db_url)
    if not source_path.exists():
        raise FileNotFoundError(f"plant database does not exist: {source_path}")
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = Path(output_path) if output_path is not None else destination_dir / f"sentinel_backup_{timestamp}.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem}_{uuid4().hex[:8]}{target.suffix}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source_path, timeout=5)) as source, closing(sqlite3.connect(temporary, timeout=5)) as destination:
            source.execute("PRAGMA busy_timeout=5000")
            destination.execute("PRAGMA foreign_keys=OFF")
            counts = {table: _copy_table(source, destination, table) for table in TABLES}
            destination.commit()
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(target.resolve()), "tables": counts, "created_at": timestamp}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None, help="SQLAlchemy plant DB URL; defaults to DB_URL")
    parser.add_argument("--output-dir", type=Path, default=runtime_paths.backup_root)
    parser.add_argument("--output", dest="output_path", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        result = backup(**vars(_parser().parse_args(argv)))
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
