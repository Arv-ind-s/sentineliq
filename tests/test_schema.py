from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text

from core.db import SchemaUpgradeRequired, create_schema, get_engine, get_session_factory
from core.models import Base, Machine


ROOT = Path(__file__).parents[1]


@pytest.fixture
def database_url() -> str:
    artifact_dir = ROOT / ".test-artifacts" / f"schema-{uuid4().hex}"
    artifact_dir.mkdir(parents=True)
    url = f"sqlite:///{artifact_dir / 'plant.db'}"
    try:
        yield url
    finally:
        get_engine(url).dispose()
        shutil.rmtree(artifact_dir, ignore_errors=True)


def test_create_schema_has_versioned_appendix_c_tables(database_url: str) -> None:
    create_schema(database_url)

    table_names = set(inspect(get_engine(database_url)).get_table_names())

    assert table_names == {
        "machines", "fault_events", "work_orders", "features", "predictions", "evidence", "diagnoses",
        "agent_runs", "alerts", "approvals", "audit_ledger", "ingest_dlq", "users", "schema_version",
    }
    assert len(Base.metadata.tables) == 14


def test_sqlite_pragmas_are_configured(database_url: str) -> None:
    create_schema(database_url)

    with get_engine(database_url).connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
        assert connection.execute(text("PRAGMA synchronous")).scalar_one() == 1
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
        assert connection.execute(text("PRAGMA cache_size")).scalar_one() == -32768
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_existing_unversioned_database_requires_explicit_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE machines (id TEXT PRIMARY KEY)")

    with pytest.raises(SchemaUpgradeRequired, match="scripts.migrate"):
        create_schema(f"sqlite:///{database_path}")


def test_concurrent_writers_wait_within_the_five_second_busy_timeout(database_url: str) -> None:
    create_schema(database_url)
    factory = get_session_factory(database_url)
    first_writer_ready = threading.Event()
    errors: list[BaseException] = []

    def first_writer() -> None:
        try:
            with factory() as session:
                session.add(Machine(id="M-001", name="First", type="pump", model="X", line="L1", install_date=0, criticality=1, rated_rpm=1000, hourly_downtime_cost_gbp=1, unit_map={}))
                session.flush()
                first_writer_ready.set()
                time.sleep(0.25)
                session.commit()
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    def second_writer() -> None:
        try:
            first_writer_ready.wait(timeout=2)
            with factory() as session:
                session.add(Machine(id="M-002", name="Second", type="pump", model="X", line="L1", install_date=0, criticality=1, rated_rpm=1000, hourly_downtime_cost_gbp=1, unit_map={}))
                session.commit()
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=first_writer), threading.Thread(target=second_writer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    with factory() as session:
        assert session.query(Machine).count() == 2


def test_schema_uses_generic_json_and_epoch_millisecond_timestamp_types() -> None:
    from sqlalchemy import BigInteger, JSON

    for table in Base.metadata.tables.values():
        for column in table.columns:
            assert column.type.__class__.__module__.startswith("sqlalchemy")
            assert "sqlite" not in column.type.__class__.__module__.lower()
    assert Base.metadata.tables["features"].c.vector.type.__class__ is JSON
    assert Base.metadata.tables["machines"].c.install_date.type.__class__ is BigInteger
    assert Base.metadata.tables["audit_ledger"].c.ts.type.__class__ is BigInteger
