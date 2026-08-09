"""Lazy SQLAlchemy engine and session factory for the plant database."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Lock
import time
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings
from core.models import Base


CURRENT_SCHEMA_VERSION = 2


class SchemaUpgradeRequired(RuntimeError):
    """Raised when an existing database must be upgraded explicitly."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_database_url: str | None = None
_lock = Lock()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite:")


def _ensure_sqlite_parent(url: str) -> None:
    if not _is_sqlite(url) or ":memory:" in url:
        return
    database_path = url.removeprefix("sqlite:///")
    if database_path.startswith("/"):
        path = Path(database_path)
    else:
        path = Path.cwd() / database_path
    path.parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(connection: object, _: object) -> None:
    cursor = connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA cache_size=-32768")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_engine(database_url: str | None = None) -> Engine:
    """Return the process-wide engine, creating it only on first use.

    ``database_url`` is primarily a test seam; production uses ``DB_URL`` via
    the validated application settings.
    """

    global _engine, _database_url
    url = database_url or settings.db_url
    with _lock:
        if _engine is not None and _database_url == url:
            return _engine
        if _engine is not None:
            _engine.dispose()
        _ensure_sqlite_parent(url)
        kwargs: dict[str, object] = {"future": True, "pool_pre_ping": True}
        if _is_sqlite(url):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 5}
        engine = create_engine(url, **kwargs)
        if _is_sqlite(url):
            event.listen(engine, "connect", _configure_sqlite)
        _engine = engine
        _database_url = url
        return engine


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """Return the lazy singleton session factory bound to the active engine."""

    global _session_factory
    engine = get_engine(database_url)
    with _lock:
        if _session_factory is None or _session_factory.kw.get("bind") is not engine:
            _session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
        return _session_factory


def dispose_engine(database_url: str | None = None) -> None:
    """Dispose and forget the process-wide engine for a database URL.

    Tests and short-lived evaluation jobs use alternate SQLite files in
    temporary directories.  Clearing the singleton references as well as the
    SQLAlchemy pool is important on Windows, where a still-referenced engine
    can keep the database file open after ``Engine.dispose()`` returns.
    """

    global _engine, _session_factory, _database_url
    url = database_url or settings.db_url
    with _lock:
        if _engine is None or _database_url != url:
            return
        engine = _engine
        _engine = None
        _session_factory = None
        _database_url = None
        engine.dispose()


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    """Yield a session and commit or roll back its transaction safely."""

    session = get_session_factory(database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_schema(database_url: str | None = None) -> None:
    """Create a fresh current schema or validate an existing current schema.

    Existing unversioned databases are never changed implicitly.  They must be
    upgraded with ``python -m scripts.migrate`` so the backup and verification
    steps cannot be bypassed by an ordinary request.
    """

    engine = get_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    application_tables = tables.intersection(set(Base.metadata.tables) - {"schema_version"})
    if application_tables and "schema_version" not in tables:
        raise SchemaUpgradeRequired(
            "database schema is unversioned; run `python -m scripts.migrate` before starting Sentinel"
        )
    if "schema_version" in tables:
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version FROM schema_version WHERE id = 1").scalar_one_or_none()
        if version != CURRENT_SCHEMA_VERSION:
            raise SchemaUpgradeRequired(
                f"database schema version {version!r} is not supported; expected {CURRENT_SCHEMA_VERSION}. "
                "Run `python -m scripts.migrate` before starting Sentinel"
            )
        Base.metadata.create_all(engine)
        return

    Base.metadata.create_all(engine)
    now_ms = int(time.time() * 1000)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO schema_version (id, version, applied_at) VALUES (1, ?, ?)",
            (CURRENT_SCHEMA_VERSION, now_ms),
        )
