"""Small SQLite cache and usage ledger for LLM calls.

The cache is deliberately independent from the plant database.  A diagnosis
cache entry is content-addressed, expires by wall-clock time, and stores the
token accounting needed by the Trust screen and evaluation harness.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CachedResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    provider: str


def evidence_bundle(messages: Sequence[Mapping[str, Any]]) -> str:
    """Return a canonical representation of a chat evidence bundle."""

    return json.dumps(list(messages), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def cache_key(prompt_version: str, model: str, bundle: str) -> str:
    """Hash the version, model, and evidence bundle into a stable cache key."""

    payload = f"{prompt_version}{model}{bundle}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class LLMCache:
    """Thread-safe SQLite cache with a call-usage ledger."""

    def __init__(self, path: str | Path, ttl_s: int = 900, clock: Any = time.time) -> None:
        self.path = Path(path)
        self.ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                text TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS llm_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                cached INTEGER NOT NULL,
                success INTEGER NOT NULL,
                error TEXT
            )"""
        )
        self._connection.commit()

    def get(self, key: str) -> CachedResponse | None:
        now = float(self._clock())
        with self._lock:
            row = self._connection.execute(
                "SELECT text, prompt_tokens, completion_tokens, model, provider, expires_at FROM llm_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if float(row[5]) <= now:
                self._connection.execute("DELETE FROM llm_cache WHERE cache_key = ?", (key,))
                self._connection.commit()
                return None
            return CachedResponse(str(row[0]), int(row[1]), int(row[2]), str(row[3]), str(row[4]))

    def put(self, key: str, response: CachedResponse) -> None:
        now = float(self._clock())
        with self._lock:
            self._connection.execute(
                """INSERT INTO llm_cache(cache_key, created_at, expires_at, text, prompt_tokens,
                   completion_tokens, model, provider) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET created_at=excluded.created_at,
                   expires_at=excluded.expires_at, text=excluded.text,
                   prompt_tokens=excluded.prompt_tokens, completion_tokens=excluded.completion_tokens,
                   model=excluded.model, provider=excluded.provider""",
                (key, now, now + self.ttl_s, response.text, response.prompt_tokens, response.completion_tokens, response.model, response.provider),
            )
            self._connection.commit()

    def record_run(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        provider: str,
        latency_ms: float,
        cached: bool,
        success: bool,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """INSERT INTO llm_runs(created_at, prompt_tokens, completion_tokens, model, provider,
                   latency_ms, cached, success, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (float(self._clock()), prompt_tokens, completion_tokens, model, provider, latency_ms, int(cached), int(success), error),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "LLMCache":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
