from __future__ import annotations

from pathlib import Path

from core.runtime_paths import ROOT, resolve_runtime_paths


def test_native_runtime_paths_preserve_repository_layout() -> None:
    paths = resolve_runtime_paths({})

    assert paths.state_root is None
    assert paths.data_root == ROOT / "data"
    assert paths.model_root == ROOT / "models"
    assert paths.erp_db == ROOT / "erp" / "erp.db"
    assert paths.index_path == ROOT / "data" / "index" / "logs.npz"
    assert paths.llm_cache_path == ROOT / "data" / "llm_cache.db"
    assert paths.agent_log_path == ROOT / "logs" / "app.jsonl"


def test_state_root_moves_all_mutable_container_state(tmp_path: Path) -> None:
    state = tmp_path / "state"
    paths = resolve_runtime_paths({"SENTINEL_STATE_ROOT": str(state)})

    assert paths.state_root == state
    assert paths.data_root == state / "data"
    assert paths.model_root == state / "models"
    assert paths.erp_db == state / "erp" / "erp.db"
    assert paths.backup_root == state / "backups"


def test_specific_overrides_take_precedence_over_state_root(tmp_path: Path) -> None:
    paths = resolve_runtime_paths(
        {
            "SENTINEL_STATE_ROOT": str(tmp_path / "state"),
            "SENTINEL_DATA_ROOT": str(tmp_path / "custom-data"),
            "SENTINEL_MODEL_ROOT": str(tmp_path / "custom-models"),
            "ERP_DB_PATH": str(tmp_path / "custom-erp.db"),
            "SENTINEL_INDEX_PATH": str(tmp_path / "custom-index.npz"),
        }
    )

    assert paths.data_root == tmp_path / "custom-data"
    assert paths.model_root == tmp_path / "custom-models"
    assert paths.erp_db == tmp_path / "custom-erp.db"
    assert paths.index_path == tmp_path / "custom-index.npz"


def test_erp_root_accepts_a_directory_or_database_path(tmp_path: Path) -> None:
    directory = resolve_runtime_paths({"SENTINEL_ERP_ROOT": str(tmp_path / "erp")})
    database = resolve_runtime_paths({"SENTINEL_ERP_ROOT": str(tmp_path / "legacy.db")})

    assert directory.erp_db == tmp_path / "erp" / "erp.db"
    assert database.erp_db == tmp_path / "legacy.db"
