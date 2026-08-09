"""Append-only, hash-chained audit ledger operations.

The audit row is deliberately hashed from the values that are persisted in the
ledger.  Keeping canonicalisation here gives every consequential event the
same representation and prevents individual callers from accidentally
creating a chain that the verifier cannot reproduce.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from threading import Lock
from time import time
from typing import Any

from sqlalchemy import select

from core.db import create_schema, session_scope
from core.models import AuditLedger


ZERO_HASH = "0" * 64
_AUDIT_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One event to append to the audit chain."""

    actor: str
    action: str
    subject: str
    payload: Mapping[str, Any]
    timestamp_ms: int | None = None


@dataclass(frozen=True, slots=True)
class AuditVerification:
    """Result returned by :func:`verify_ledger`."""

    intact: bool
    entries: int
    checked_entries: int
    first_invalid_seq: int | None


def _json_ready(value: Any) -> Any:
    """Convert common application values into JSON-compatible primitives."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(child) for child in value]
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump(mode="json"))
    return str(value)


def canonical_json(*, ts: int, actor: str, action: str, subject: str, payload: Mapping[str, Any], prev_hash: str) -> str:
    """Return the stable JSON representation used by the ledger hash.

    ``seq`` is intentionally not included: it is allocated by SQLite during
    the append and the chain already binds ordering through ``prev_hash``.
    The persisted fields and this representation use the same millisecond
    timestamp and JSON payload.
    """

    entry = {
        "ts": int(ts),
        "actor": str(actor),
        "action": str(action),
        "subject": str(subject),
        "payload": _json_ready(payload),
        "prev_hash": str(prev_hash),
    }
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def entry_hash(*, prev_hash: str, ts: int, actor: str, action: str, subject: str, payload: Mapping[str, Any]) -> str:
    """Compute the hash for one persisted audit entry."""

    canonical = canonical_json(ts=ts, actor=actor, action=action, subject=subject, payload=payload, prev_hash=prev_hash)
    return hashlib.sha256(f"{prev_hash}{canonical}".encode("utf-8")).hexdigest()


def _event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("audit payload must be a mapping")
    value = _json_ready(payload)
    if not isinstance(value, dict):  # pragma: no cover - guarded by Mapping above
        raise TypeError("audit payload must serialize to an object")
    return value


def append_events(events: Iterable[AuditEvent], *, database_url: str | None = None) -> list[AuditLedger]:
    """Append events atomically and return the resulting ORM rows.

    A process-wide lock serialises readers and writers of the chain.  SQLite's
    WAL mode then keeps the transaction durable while allowing normal audit
    reads to proceed.  The function is also used for batches such as a single
    guardrail rejection, so a batch cannot leave an intermediate chain.
    """

    values = list(events)
    if not values:
        return []

    with _AUDIT_LOCK:
        create_schema(database_url)
        with session_scope(database_url) as session:
            previous = session.scalars(select(AuditLedger.hash).order_by(AuditLedger.seq.desc())).first() or ZERO_HASH
            rows: list[AuditLedger] = []
            for event in values:
                timestamp_ms = int(event.timestamp_ms if event.timestamp_ms is not None else time() * 1000)
                actor = str(event.actor)
                action = str(event.action)
                subject = str(event.subject)
                payload = _event_payload(event.payload)
                current_hash = entry_hash(prev_hash=previous, ts=timestamp_ms, actor=actor, action=action, subject=subject, payload=payload)
                row = AuditLedger(
                    ts=timestamp_ms,
                    actor=actor,
                    action=action,
                    subject=subject,
                    payload_json=payload,
                    prev_hash=previous,
                    hash=current_hash,
                )
                session.add(row)
                rows.append(row)
                previous = current_hash
            session.flush()
            return rows


def append_event(
    *,
    actor: str,
    action: str,
    subject: str,
    payload: Mapping[str, Any] | None = None,
    database_url: str | None = None,
    timestamp_ms: int | None = None,
) -> AuditLedger:
    """Append one event to the durable audit chain."""

    return append_events(
        [AuditEvent(actor=actor, action=action, subject=subject, payload=payload or {}, timestamp_ms=timestamp_ms)],
        database_url=database_url,
    )[0]


def verify_ledger(*, database_url: str | None = None) -> AuditVerification:
    """Recompute the complete chain and identify the first broken sequence."""

    with _AUDIT_LOCK:
        create_schema(database_url)
        with session_scope(database_url) as session:
            rows = session.scalars(select(AuditLedger).order_by(AuditLedger.seq)).all()

    total = len(rows)
    previous = ZERO_HASH
    expected_seq = 1
    checked = 0
    for row in rows:
        if row.seq != expected_seq:
            return AuditVerification(False, total, checked, expected_seq)
        payload = row.payload_json if isinstance(row.payload_json, Mapping) else {"value": row.payload_json}
        expected_hash = entry_hash(
            prev_hash=previous,
            ts=row.ts,
            actor=row.actor,
            action=row.action,
            subject=row.subject,
            payload=payload,
        )
        if row.prev_hash != previous or row.hash != expected_hash:
            return AuditVerification(False, total, checked, row.seq)
        previous = row.hash
        expected_seq += 1
        checked += 1
    return AuditVerification(True, total, checked, None)


__all__ = [
    "AuditEvent",
    "AuditVerification",
    "ZERO_HASH",
    "append_event",
    "append_events",
    "canonical_json",
    "entry_hash",
    "verify_ledger",
]
