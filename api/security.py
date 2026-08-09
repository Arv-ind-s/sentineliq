"""Small, process-local controls for the public HTTP boundary."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Final

from fastapi import Request

from api.auth import COOKIE_NAME, verify_token


DEFAULT_RATE_PER_MINUTE: Final = 120
DIAGNOSE_RATE_PER_MINUTE: Final = 10
INGEST_RATE_PER_MINUTE: Final = 60
DEFAULT_BODY_LIMIT_BYTES: Final = 64 * 1024
INGEST_BODY_LIMIT_BYTES: Final = 2 * 1024 * 1024


class RequestBodyTooLarge(ValueError):
    """Raised when a streamed request exceeds its endpoint body limit."""

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(f"request body exceeds {limit_bytes} bytes")
        self.limit_bytes = limit_bytes


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    """Thread-safe token buckets with one capacity per request policy."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, int], _Bucket] = {}
        self._lock = Lock()

    def allow(self, key: str, requests_per_minute: int) -> bool:
        now = self._clock()
        refill_per_second = requests_per_minute / 60.0
        bucket_key = (key, requests_per_minute)
        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                self._buckets[bucket_key] = _Bucket(tokens=requests_per_minute - 1, updated_at=now)
                return True
            bucket.tokens = min(requests_per_minute, bucket.tokens + max(0.0, now - bucket.updated_at) * refill_per_second)
            bucket.updated_at = now
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True

    def clear(self) -> None:
        """Clear all buckets; useful for isolated process/test lifecycles."""

        with self._lock:
            self._buckets.clear()


def rate_limit_for_path(path: str) -> int:
    if path == "/ingest/batch":
        return INGEST_RATE_PER_MINUTE
    if path == "/diagnose" or path.endswith("/diagnose"):
        return DIAGNOSE_RATE_PER_MINUTE
    return DEFAULT_RATE_PER_MINUTE


def body_limit_for_path(path: str) -> int:
    return INGEST_BODY_LIMIT_BYTES if path == "/ingest/batch" else DEFAULT_BODY_LIMIT_BYTES


def request_identity(request: Request) -> str:
    """Resolve an authenticated user, falling back to the client address."""

    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            return f"user:{verify_token(token).username}"
        except Exception:
            pass
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://localhost:8000; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
}


__all__ = [
    "DEFAULT_BODY_LIMIT_BYTES",
    "DEFAULT_RATE_PER_MINUTE",
    "DIAGNOSE_RATE_PER_MINUTE",
    "INGEST_BODY_LIMIT_BYTES",
    "INGEST_RATE_PER_MINUTE",
    "RequestBodyTooLarge",
    "SECURITY_HEADERS",
    "TokenBucketRateLimiter",
    "body_limit_for_path",
    "rate_limit_for_path",
    "request_identity",
]
