"""Small process-local counters used by the API observability contract.

The durable agent and ingest measurements are read from the plant database at
the API boundary.  Request and circuit-breaker transitions, however, happen
in the serving process and are intentionally kept here with a lock so the
metrics endpoint remains safe under concurrent ASGI requests.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RuntimeMetricsSnapshot:
    """Immutable snapshot of measurements that are not persisted in SQLite."""

    requests: int
    errors_by_code: dict[str, int]
    circuit_breaker_trips: dict[str, int]


class MetricsRegistry:
    """Thread-safe process-local counters for the health/metrics endpoints."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests = 0
        self._errors_by_code: Counter[str] = Counter()
        self._circuit_breaker_trips: Counter[str] = Counter()

    def record_request(self, status_code: int, error_code: str | None = None) -> None:
        """Record one completed request and, for failures, its error code."""

        with self._lock:
            self._requests += 1
            if status_code >= 400:
                self._errors_by_code[error_code or str(status_code)] += 1

    def record_breaker_trip(self, dependency: str) -> None:
        """Record a transition into an open dependency circuit."""

        with self._lock:
            self._circuit_breaker_trips[dependency] += 1

    def snapshot(self) -> RuntimeMetricsSnapshot:
        """Return a copy that callers can safely enrich and serialize."""

        with self._lock:
            return RuntimeMetricsSnapshot(
                requests=self._requests,
                errors_by_code=dict(self._errors_by_code),
                circuit_breaker_trips=dict(self._circuit_breaker_trips),
            )


__all__ = ["MetricsRegistry", "RuntimeMetricsSnapshot"]
