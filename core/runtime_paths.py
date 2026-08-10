"""Resolve writable SentinelIQ runtime state without hiding source packages.

Native development keeps the historical repository-relative layout. A
container deployment can set ``SENTINEL_STATE_ROOT`` to place every generated
or mutable artifact below one mounted directory. More specific environment
variables always win, preserving existing script and test seams.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Fully resolved locations for generated and mutable application state."""

    state_root: Path | None
    data_root: Path
    model_root: Path
    erp_db: Path
    index_path: Path
    llm_cache_path: Path
    agent_log_path: Path
    backup_root: Path

    @property
    def sensor_root(self) -> Path:
        return self.data_root / "sensors"

    @property
    def plant_db(self) -> Path:
        return self.data_root / "plant.db"


def _resolved(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def resolve_runtime_paths(environment: Mapping[str, str] | None = None) -> RuntimePaths:
    """Resolve paths from a supplied environment or the current process."""

    env = os.environ if environment is None else environment
    raw_state_root = env.get("SENTINEL_STATE_ROOT", "").strip()
    state_root = _resolved(raw_state_root) if raw_state_root else None

    data_default = state_root / "data" if state_root else ROOT / "data"
    model_default = state_root / "models" if state_root else ROOT / "models"
    erp_default = state_root / "erp" / "erp.db" if state_root else ROOT / "erp" / "erp.db"
    log_default = state_root / "logs" / "app.jsonl" if state_root else ROOT / "logs" / "app.jsonl"
    backup_default = state_root / "backups" if state_root else ROOT / "backups"

    data_root = _resolved(env.get("SENTINEL_DATA_ROOT", "") or data_default)
    model_root = _resolved(env.get("SENTINEL_MODEL_ROOT", "") or model_default)

    raw_erp_db = env.get("ERP_DB_PATH", "").strip()
    raw_erp_root = env.get("SENTINEL_ERP_ROOT", "").strip()
    if raw_erp_db:
        erp_db = _resolved(raw_erp_db)
    elif raw_erp_root:
        candidate = _resolved(raw_erp_root)
        erp_db = candidate if candidate.suffix == ".db" else candidate / "erp.db"
    else:
        erp_db = erp_default.resolve()

    return RuntimePaths(
        state_root=state_root,
        data_root=data_root,
        model_root=model_root,
        erp_db=erp_db,
        index_path=_resolved(env.get("SENTINEL_INDEX_PATH", "") or data_root / "index" / "logs.npz"),
        llm_cache_path=_resolved(env.get("SENTINEL_LLM_CACHE_PATH", "") or data_root / "llm_cache.db"),
        agent_log_path=_resolved(env.get("SENTINEL_LOG_PATH", "") or log_default),
        backup_root=_resolved(env.get("SENTINEL_BACKUP_ROOT", "") or backup_default),
    )


runtime_paths = resolve_runtime_paths()


__all__ = ["ROOT", "RuntimePaths", "resolve_runtime_paths", "runtime_paths"]
