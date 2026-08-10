"""Create and validate the persistent state used by Docker Compose.

The initializer is intentionally conservative: only an empty state volume is
seeded. A versioned marker makes subsequent starts idempotent, while partial,
unrecognized, or incompatible state is rejected without deleting user data.
"""

from __future__ import annotations

import json
import argparse
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import SEEDED_USERS
from core.db import CURRENT_SCHEMA_VERSION, dispose_engine
from core.predict import load_verified_predictor
from core.retrieval import NumpyVectorStore
from data.generate import verify_signal
from erp.history import HISTORY_SIZE
from scripts.seed import seed


STATE_FORMAT_VERSION = 1
MARKER_NAME = ".sentineliq-state.json"
STAGING_PREFIX = ".sentineliq-init-"
STATE_DIRECTORIES = ("data", "models", "erp", "logs", "backups")


class StateInitializationError(RuntimeError):
    """Raised when persistent state cannot be initialized safely."""


@dataclass(frozen=True, slots=True)
class StateLayout:
    root: Path
    data_root: Path
    model_root: Path
    erp_db: Path
    index_path: Path
    marker_path: Path

    @classmethod
    def at(cls, root: str | Path) -> "StateLayout":
        resolved = Path(root).resolve()
        return cls(
            root=resolved,
            data_root=resolved / "data",
            model_root=resolved / "models",
            erp_db=resolved / "erp" / "erp.db",
            index_path=resolved / "data" / "index" / "logs.npz",
            marker_path=resolved / MARKER_NAME,
        )


def _required_paths(layout: StateLayout) -> tuple[Path, ...]:
    return (
        layout.data_root / "metadata.json",
        layout.data_root / "scenario_manifest.json",
        layout.data_root / "DEMO_NOTES.md",
        layout.data_root / "plant.db",
        layout.data_root / "sensors",
        layout.model_root / "MANIFEST.json",
        layout.erp_db,
        layout.index_path,
    )


def _read_marker(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateInitializationError(f"state marker is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format_version") != STATE_FORMAT_VERSION:
        found = payload.get("format_version") if isinstance(payload, dict) else None
        raise StateInitializationError(
            f"state format {found!r} is incompatible with format {STATE_FORMAT_VERSION}; "
            "back up the volume and run the documented migration or reset command"
        )
    return payload


def _validate_core_database(path: Path) -> None:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            version = connection.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            if version is None or int(version[0]) != CURRENT_SCHEMA_VERSION:
                found = None if version is None else version[0]
                raise StateInitializationError(
                    f"plant database schema {found!r} is incompatible with schema {CURRENT_SCHEMA_VERSION}; "
                    "run the documented migration before restarting"
                )
            users = {str(row[0]) for row in connection.execute("SELECT username FROM users")}
    except sqlite3.Error as exc:
        raise StateInitializationError(f"plant database validation failed: {exc}") from exc
    missing_users = sorted({username for username, _ in SEEDED_USERS}.difference(users))
    if missing_users:
        raise StateInitializationError(f"plant database is missing demo users: {', '.join(missing_users)}")


def _validate_erp_database(path: Path) -> int:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = {"assets", "work_orders", "idempotency_keys"}.difference(tables)
            if missing:
                raise StateInitializationError(f"ERP database is missing tables: {', '.join(sorted(missing))}")
            count = int(connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0])
    except sqlite3.Error as exc:
        raise StateInitializationError(f"ERP database validation failed: {exc}") from exc
    if count < HISTORY_SIZE:
        raise StateInitializationError(f"ERP history is incomplete: expected at least {HISTORY_SIZE} work orders, found {count}")
    return count


def validate_state(layout: StateLayout) -> dict[str, int]:
    """Validate every persisted subsystem without modifying or reseeding it."""

    missing = [str(path.relative_to(layout.root)) for path in _required_paths(layout) if not path.exists()]
    if missing:
        raise StateInitializationError(f"persistent state is incomplete; missing: {', '.join(missing)}")
    verify_signal(layout.data_root)
    load_verified_predictor(layout.model_root)
    _validate_core_database(layout.data_root / "plant.db")
    work_orders = _validate_erp_database(layout.erp_db)
    index = NumpyVectorStore.load(layout.index_path)
    if not index.item_ids:
        raise StateInitializationError("retrieval index contains no work orders")
    return {"work_orders": work_orders, "index_items": len(index.item_ids)}


def _checkpoint_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()


def _remove_abandoned_staging(root: Path) -> None:
    for child in root.iterdir():
        if child.name.startswith(STAGING_PREFIX) and child.is_dir():
            shutil.rmtree(child)


def _unrecognized_entries(root: Path) -> list[str]:
    return sorted(
        child.name
        for child in root.iterdir()
        if child.name != MARKER_NAME and not child.name.startswith(STAGING_PREFIX) and child.name != "lost+found"
    )


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    for current, directories, files in os.walk(root):
        os.chown(current, uid, gid)
        for name in (*directories, *files):
            os.chown(Path(current) / name, uid, gid, follow_symlinks=False)


def _write_marker(layout: StateLayout, validation: dict[str, int], uid: int | None, gid: int | None) -> None:
    payload = {
        "format_version": STATE_FORMAT_VERSION,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "demo_integrity_verified": True,
        "created_at": datetime.now(UTC).isoformat(),
        **validation,
    }
    temporary = layout.root / f"{MARKER_NAME}.tmp"
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, layout.marker_path)
    if uid is not None and gid is not None:
        os.chown(layout.marker_path, uid, gid)


def initialize_state(
    state_root: str | Path,
    *,
    seed_runner: Callable[..., Any] = seed,
    verifier: Callable[[StateLayout], dict[str, int]] = validate_state,
    owner_uid: int | None = None,
    owner_gid: int | None = None,
    adopt_existing: bool = False,
) -> str:
    """Validate a committed state volume or seed an empty one."""

    layout = StateLayout.at(state_root)
    layout.root.mkdir(parents=True, exist_ok=True)
    _remove_abandoned_staging(layout.root)

    if layout.marker_path.exists():
        _read_marker(layout.marker_path)
        verifier(layout)
        if owner_uid is not None and owner_gid is not None:
            _chown_tree(layout.root, owner_uid, owner_gid)
        return "ready"

    entries = _unrecognized_entries(layout.root)
    if entries:
        if adopt_existing:
            for name in ("logs", "backups"):
                (layout.root / name).mkdir(parents=True, exist_ok=True)
            validation = verifier(layout)
            _write_marker(layout, validation, None, None)
            if owner_uid is not None and owner_gid is not None:
                _chown_tree(layout.root, owner_uid, owner_gid)
            return "adopted"
        raise StateInitializationError(
            "refusing to overwrite unrecognized or partial persistent state "
            f"({', '.join(entries)}); back it up, migrate it, or explicitly reset with `docker compose down -v`"
        )

    staging = layout.root / f"{STAGING_PREFIX}{uuid4().hex}"
    staging_layout = StateLayout.at(staging)
    staging.mkdir(mode=0o700)
    try:
        for name in ("logs", "backups"):
            (staging / name).mkdir(parents=True, exist_ok=True)
        seed_runner(
            data_root=staging_layout.data_root,
            artifact_root=staging_layout.model_root,
            erp_path=staging_layout.erp_db,
            index_path=staging_layout.index_path,
        )
        database_url = f"sqlite:///{staging_layout.data_root / 'plant.db'}"
        dispose_engine(database_url)
        _checkpoint_database(staging_layout.data_root / "plant.db")
        _checkpoint_database(staging_layout.erp_db)
        validation = verifier(staging_layout)

        for name in STATE_DIRECTORIES:
            os.replace(staging / name, layout.root / name)
        staging.rmdir()
        _write_marker(layout, validation, None, None)
        if owner_uid is not None and owner_gid is not None:
            _chown_tree(layout.root, owner_uid, owner_gid)
        return "seeded"
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="validate and mark a complete existing state layout without reseeding it",
    )
    args = parser.parse_args()
    raw_root = os.getenv("SENTINEL_STATE_ROOT", "").strip()
    if not raw_root:
        print("container initialization failed: SENTINEL_STATE_ROOT is required", file=sys.stderr)
        return 1
    uid = int(os.getenv("SENTINEL_RUNTIME_UID", "10001"))
    gid = int(os.getenv("SENTINEL_RUNTIME_GID", "10001"))
    try:
        result = initialize_state(raw_root, owner_uid=uid, owner_gid=gid, adopt_existing=args.adopt_existing)
    except (OSError, ValueError, StateInitializationError) as exc:
        print(f"container initialization failed: {exc}", file=sys.stderr)
        return 1
    print(f"SentinelIQ persistent state is {result}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
