from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.container_init import (
    MARKER_NAME,
    STATE_DIRECTORIES,
    STATE_FORMAT_VERSION,
    StateInitializationError,
    StateLayout,
    initialize_state,
)


def _fake_seed(**paths: Path) -> None:
    data_root = paths["data_root"]
    artifact_root = paths["artifact_root"]
    erp_path = paths["erp_path"]
    index_path = paths["index_path"]
    for path in (data_root, artifact_root, erp_path.parent, index_path.parent):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("metadata.json", "scenario_manifest.json", "DEMO_NOTES.md"):
        (data_root / name).write_text("fixture", encoding="utf-8")
    sqlite3.connect(data_root / "plant.db").close()
    (data_root / "sensors").mkdir()
    (artifact_root / "MANIFEST.json").write_text("{}", encoding="utf-8")
    sqlite3.connect(erp_path).close()
    index_path.write_text("fixture", encoding="utf-8")


def _fake_verifier(layout: StateLayout) -> dict[str, int]:
    assert all((layout.root / name).exists() for name in ("data", "models", "erp", "logs", "backups"))
    return {"work_orders": 480, "index_items": 480}


def test_empty_state_is_seeded_and_committed(tmp_path: Path) -> None:
    result = initialize_state(tmp_path, seed_runner=_fake_seed, verifier=_fake_verifier)

    assert result == "seeded"
    marker = json.loads((tmp_path / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["format_version"] == STATE_FORMAT_VERSION
    assert marker["demo_integrity_verified"] is True
    assert all((tmp_path / name).exists() for name in STATE_DIRECTORIES)


def test_committed_state_is_validated_without_reseeding(tmp_path: Path) -> None:
    initialize_state(tmp_path, seed_runner=_fake_seed, verifier=_fake_verifier)

    def unexpected_seed(**_: Path) -> None:
        raise AssertionError("seed should not run for committed state")

    assert initialize_state(tmp_path, seed_runner=unexpected_seed, verifier=_fake_verifier) == "ready"


def test_partial_state_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "plant.db").write_text("important", encoding="utf-8")

    with pytest.raises(StateInitializationError, match="refusing to overwrite"):
        initialize_state(tmp_path, seed_runner=_fake_seed, verifier=_fake_verifier)

    assert (tmp_path / "data" / "plant.db").read_text(encoding="utf-8") == "important"


def test_incompatible_marker_fails_without_reseeding(tmp_path: Path) -> None:
    (tmp_path / MARKER_NAME).write_text('{"format_version": 999}\n', encoding="utf-8")

    with pytest.raises(StateInitializationError, match="incompatible"):
        initialize_state(tmp_path, seed_runner=_fake_seed, verifier=_fake_verifier)


def test_complete_unmarked_state_requires_explicit_adoption(tmp_path: Path) -> None:
    initialize_state(tmp_path, seed_runner=_fake_seed, verifier=_fake_verifier)
    (tmp_path / MARKER_NAME).unlink()

    def unexpected_seed(**_: Path) -> None:
        raise AssertionError("adoption must not seed")

    result = initialize_state(
        tmp_path,
        seed_runner=unexpected_seed,
        verifier=_fake_verifier,
        adopt_existing=True,
    )

    assert result == "adopted"
    assert (tmp_path / MARKER_NAME).is_file()


def test_failed_seed_leaves_no_committed_state(tmp_path: Path) -> None:
    def failed_seed(**_: Path) -> None:
        raise RuntimeError("training failed")

    with pytest.raises(RuntimeError, match="training failed"):
        initialize_state(tmp_path, seed_runner=failed_seed, verifier=_fake_verifier)

    assert not (tmp_path / MARKER_NAME).exists()
    assert not any(child.name.startswith(".sentineliq-init-") for child in tmp_path.iterdir())
