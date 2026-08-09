"""Build and verify the complete offline SentinelPdM demo state.

The seed is deliberately a small, ordered pipeline.  Each stage writes only
regenerable local state, so rerunning it is safe and leaves the committed
configuration untouched:

``generate -> verify_signal -> train -> ERP history/index -> users -> verify
-> demo-integrity test``

The final pytest invocation is part of the seed contract.  A model that does
not make the live demo scenarios behave as documented is not a successful
seed, even when the lower-level files were generated correctly.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import seed_users
from core.config import ROOT, settings
from core.retrieval import DEFAULT_INDEX_PATH, MaintenanceLogRetriever
from data.generate import generate, verify_signal
from scripts.seed_erp_history import seed_erp_history
from scripts.backfill_predictions import backfill, seed_demo_approval
from scripts.train import train


DEMO_TEST = ROOT / "tests" / "test_demo_integrity.py"


@dataclass(frozen=True)
class SeedResult:
    """Summary of the stages performed by :func:`seed`."""

    generated: bool
    trained: bool
    retrieval_index: Path
    users_seeded: bool


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


def _required_outputs(data_root: Path, artifact_root: Path, erp_path: Path, index_path: Path) -> tuple[Path, ...]:
    return (
        data_root / "metadata.json",
        data_root / "scenario_manifest.json",
        data_root / "plant.db",
        data_root / "sensors",
        artifact_root / "MANIFEST.json",
        erp_path,
        index_path,
    )


def _is_stale(data_root: Path, artifact_root: Path, erp_path: Path, index_path: Path) -> bool:
    outputs = _required_outputs(data_root, artifact_root, erp_path, index_path)
    if any(not path.exists() for path in outputs):
        return True

    source_files = (
        ROOT / "data" / "generate.py",
        ROOT / "scripts" / "train.py",
        ROOT / "scripts" / "seed_erp_history.py",
        ROOT / "core" / "retrieval.py",
        DEMO_TEST,
    )
    existing_sources = [path for path in source_files if path.exists()]
    oldest_output = min(path.stat().st_mtime for path in outputs)
    return bool(existing_sources and max(path.stat().st_mtime for path in existing_sources) > oldest_output)


def _run_demo_integrity(data_root: Path, artifact_root: Path) -> None:
    if not DEMO_TEST.is_file():
        raise FileNotFoundError(f"post-training demo-integrity test is missing: {DEMO_TEST}")

    environment = os.environ.copy()
    environment["SENTINEL_DATA_ROOT"] = str(data_root.resolve())
    environment["SENTINEL_MODEL_ROOT"] = str(artifact_root.resolve())
    environment["PYTHONUNBUFFERED"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(DEMO_TEST), "-q"],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"post-training demo-integrity test failed with exit code {completed.returncode}")


def seed(
    *,
    data_root: str | Path = ROOT / "data",
    artifact_root: str | Path = ROOT / settings.predict.artifacts_dir,
    erp_path: str | Path = ROOT / "erp" / "erp.db",
    index_path: str | Path = DEFAULT_INDEX_PATH,
    if_stale: bool = False,
    verify_only: bool = False,
) -> SeedResult:
    """Create or validate demo state, raising on any failed stage."""

    data_path = Path(data_root).resolve()
    model_path = Path(artifact_root).resolve()
    erp_database = Path(erp_path).resolve()
    retrieval_index = Path(index_path).resolve()

    stale = _is_stale(data_path, model_path, erp_database, retrieval_index)
    if verify_only:
        missing = [str(path) for path in _required_outputs(data_path, model_path, erp_database, retrieval_index) if not path.exists()]
        if missing:
            raise FileNotFoundError(f"cannot verify an incomplete seed; missing: {', '.join(missing)}")
        verify_signal(data_path)
        _run_demo_integrity(data_path, model_path)
        return SeedResult(False, False, retrieval_index, False)

    if if_stale and not stale:
        print("Seed outputs are current; running verification only.")
        verify_signal(data_path)
        _run_demo_integrity(data_path, model_path)
        return SeedResult(False, False, retrieval_index, False)

    print(f"Generating deterministic plant data under {data_path}...")
    generate(data_path)
    verify_signal(data_path)

    print(f"Training verified models under {model_path}...")
    train(data_path, model_path)

    print(f"Seeding ERP history under {erp_database}...")
    core_database_url = _database_url(data_path / "plant.db")
    seed_erp_history(
        erp_database_path=erp_database,
        core_database_url=core_database_url,
        fixture_path=data_path / "erp_injection_fixtures.json",
    )
    retriever = MaintenanceLogRetriever(erp_database)
    retriever.vector_store.persist(retrieval_index)

    print("Seeding local users...")
    seed_users(core_database_url)

    print("Backfilling time-indexed ledger predictions and the real demo approval...")
    backfill(core_database_url, data_path)
    seed_demo_approval(core_database_url)

    _run_demo_integrity(data_path, model_path)
    return SeedResult(True, True, retrieval_index, True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--if-stale", action="store_true", help="regenerate and retrain only when seed outputs are stale")
    parser.add_argument("--verify-only", action="store_true", help="verify existing data, artifacts, and demo-integrity output without writing")
    args = parser.parse_args()
    seed(if_stale=args.if_stale, verify_only=args.verify_only)
    print("Seed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
