"""Durable quarantine state backed by the append-only audit ledger."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from core.audit import append_event
from core.db import create_schema, session_scope
from core.models import AuditLedger
from core.redaction import redact


QUARANTINE_ACTION = "injection_quarantine"
RELEASE_ACTION = "injection_release"


def make_quarantine_id(workorder_id: str, text: str) -> str:
    """Return a stable, non-sensitive identifier for one quarantined note."""

    digest = hashlib.sha256(f"{workorder_id}\0{text}".encode("utf-8")).hexdigest()[:24]
    return f"Q-{digest}"


def _timestamp(ts: int) -> str:
    return datetime.fromtimestamp(int(ts) / 1000, tz=UTC).isoformat()


def _payload(row: AuditLedger) -> dict[str, Any]:
    value = row.payload_json
    return dict(value) if isinstance(value, Mapping) else {}


def list_quarantines(*, database_url: str | None = None, include_released: bool = True) -> list[dict[str, Any]]:
    """Return the latest visible state of every injection quarantine record."""

    create_schema(database_url)
    with session_scope(database_url) as session:
        rows = session.scalars(
            select(AuditLedger).where(AuditLedger.action.in_((QUARANTINE_ACTION, RELEASE_ACTION))).order_by(AuditLedger.seq)
        ).all()

    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _payload(row)
        quarantine_id = str(payload.get("quarantine_id") or row.subject)
        if row.action == QUARANTINE_ACTION:
            records[quarantine_id] = {
                "quarantine_id": quarantine_id,
                "workorder_id": str(payload.get("workorder_id", row.subject)),
                "machine_id": str(payload.get("machine_id", "")),
                "status": "quarantined",
                "patterns": [str(item) for item in payload.get("patterns", [])],
                "text": str(payload.get("text", "")),
                "audit_seq": int(row.seq),
                "detected_at": _timestamp(row.ts),
                "released_at": None,
                "released_by": None,
                "release_reason": None,
            }
        elif quarantine_id in records:
            record = records[quarantine_id]
            record.update(
                {
                    "status": "released",
                    "released_at": _timestamp(row.ts),
                    "released_by": str(payload.get("released_by", row.actor)),
                    "release_reason": str(payload.get("reason", "")),
                }
            )
    values = sorted(records.values(), key=lambda item: (item["detected_at"], item["quarantine_id"]), reverse=True)
    return values if include_released else [item for item in values if item["status"] == "quarantined"]


def record_quarantine(
    *,
    workorder_id: str,
    machine_id: str,
    text: str,
    patterns: Sequence[str],
    database_url: str | None = None,
) -> dict[str, Any]:
    """Persist a quarantine event without retaining the original note."""

    safe_text = redact(text)
    quarantine_id = make_quarantine_id(workorder_id, safe_text)
    existing = next(
        (item for item in list_quarantines(database_url=database_url) if item["quarantine_id"] == quarantine_id),
        None,
    )
    if existing is not None:
        return existing
    row = append_event(
        actor="injection_scanner",
        action=QUARANTINE_ACTION,
        subject=workorder_id,
        payload={
            "quarantine_id": quarantine_id,
            "workorder_id": workorder_id,
            "machine_id": machine_id,
            "patterns": list(dict.fromkeys(str(pattern) for pattern in patterns)),
            "text": safe_text,
            "status": "quarantined",
        },
        database_url=database_url,
    )
    return {
        "quarantine_id": quarantine_id,
        "workorder_id": workorder_id,
        "machine_id": machine_id,
        "status": "quarantined",
        "patterns": list(dict.fromkeys(str(pattern) for pattern in patterns)),
        "text": safe_text,
        "audit_seq": int(row.seq),
        "detected_at": _timestamp(row.ts),
        "released_at": None,
        "released_by": None,
        "release_reason": None,
    }


def release_quarantine(
    quarantine_id: str,
    *,
    actor: str,
    reason: str,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Record a human release decision; it never re-enables prompt inclusion."""

    current = next(
        (item for item in list_quarantines(database_url=database_url) if item["quarantine_id"] == quarantine_id),
        None,
    )
    if current is None:
        raise KeyError(quarantine_id)
    if current["status"] == "released":
        return current
    append_event(
        actor=actor,
        action=RELEASE_ACTION,
        subject=current["workorder_id"],
        payload={
            "quarantine_id": quarantine_id,
            "workorder_id": current["workorder_id"],
            "reason": reason,
            "released_by": actor,
            "status": "released",
        },
        database_url=database_url,
    )
    return next(
        item for item in list_quarantines(database_url=database_url) if item["quarantine_id"] == quarantine_id
    )


# Concise aliases for route and integration callers.
quarantine = record_quarantine


__all__ = [
    "QUARANTINE_ACTION",
    "RELEASE_ACTION",
    "list_quarantines",
    "make_quarantine_id",
    "quarantine",
    "record_quarantine",
    "release_quarantine",
]
