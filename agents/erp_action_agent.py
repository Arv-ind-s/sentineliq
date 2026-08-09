"""Approval-gated writes to the separate ERP service.

This module is intentionally the only place in the application that owns an
ERP write client.  The approval check happens against the plant database
before the HTTP request is made; prompts, route payloads, and callers cannot
grant themselves write authority.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agents.base import AgentResult, RunContext
from core.audit import append_event
from core.config import settings
from core.db import create_schema, session_scope
from core.models import Approval, Prediction, User, WorkOrder


logger = logging.getLogger(__name__)
_DEFAULT_ERP_API_KEY = "dev-erp-key"
_PENDING_TABLE = "pending_erp_sync"
_PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_erp_sync (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id VARCHAR(64) NOT NULL UNIQUE,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    last_error TEXT,
    created_ts BIGINT NOT NULL,
    updated_ts BIGINT NOT NULL,
    next_attempt_ts BIGINT NOT NULL
)
"""


class ERPActionError(RuntimeError):
    """Base class for errors raised while executing an approved ERP action."""


class ERPTransientError(ERPActionError):
    """A timeout or 5xx response that is safe to retry."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ERPClientError(ERPActionError):
    """A permanent ERP response, including all 4xx responses."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ERPClient(Protocol):
    """Minimal write seam used by :class:`ERPActionAgent`."""

    def create_work_order(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]: ...


class HTTPERPClient:
    """HTTP adapter for the mock or production-compatible ERP endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_s: float,
        http_client: httpx.Client | Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._http = http_client or httpx.Client(timeout=timeout_s)
        self._owns_http = http_client is None

    def create_work_order(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        url = urljoin(f"{self.base_url}/", "erp/v1/workorders")
        try:
            response = self._http.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-ERP-Scopes": "workorder:write",
                    "Idempotency-Key": idempotency_key,
                },
                json=dict(payload),
                timeout=self.timeout_s,
            )
        except (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError) as exc:
            raise ERPTransientError(f"ERP transport failed: {exc}") from exc

        status_code = int(getattr(response, "status_code", 0))
        if status_code == 0 or status_code >= 500:
            raise ERPTransientError(f"ERP returned HTTP {status_code}", status_code=status_code)  # type: ignore[call-arg]
        if status_code >= 400:
            raise ERPClientError(f"ERP rejected work order with HTTP {status_code}", status_code=status_code)
        try:
            result = response.json()
        except (TypeError, ValueError) as exc:
            raise ERPClientError("ERP returned invalid JSON", status_code=status_code) from exc
        if not isinstance(result, Mapping):
            raise ERPClientError("ERP work-order response must be an object", status_code=status_code)
        return {str(key): value for key, value in result.items()}

    def close(self) -> None:
        if self._owns_http and hasattr(self._http, "close"):
            self._http.close()


# Keep the adapter name familiar to callers that use the read-only maintenance
# agent's ERP seam.
_HttpERPClient = HTTPERPClient


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(child) for child in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump(mode="json"))
    return str(value)


def _append_audit_event(
    *,
    action: str,
    subject: str,
    payload: Mapping[str, Any],
    actor: str,
    database_url: str | None,
) -> None:
    """Append an ERP security or write event to the shared audit ledger."""

    append_event(action=action, subject=subject, payload=payload, actor=actor, database_url=database_url)


class _PendingSyncStore:
    """Durable queue storage kept outside the fixed 13-table ORM contract."""

    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            create_schema(self.database_url)
            with session_scope(self.database_url) as session:
                session.execute(text(_PENDING_SCHEMA))
            self._schema_ready = True

    def enqueue(self, prediction_id: str, idempotency_key: str, payload: Mapping[str, Any], error: str, *, attempts: int, delay_s: float) -> None:
        self._ensure_schema()
        now = int(time.time() * 1000)
        next_attempt = now + max(0, int(delay_s * 1000))
        encoded = json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":"))
        with session_scope(self.database_url) as session:
            existing = session.execute(
                text(f"SELECT id, attempts FROM {_PENDING_TABLE} WHERE idempotency_key = :key"),
                {"key": idempotency_key},
            ).mappings().first()
            if existing:
                session.execute(
                    text(
                        f"UPDATE {_PENDING_TABLE} SET payload_json = :payload, attempts = :attempts, "
                        "status = 'pending', last_error = :error, updated_ts = :updated, next_attempt_ts = :next "
                        "WHERE id = :id"
                    ),
                    {"payload": encoded, "attempts": int(existing["attempts"]) + attempts, "error": error, "updated": now, "next": next_attempt, "id": existing["id"]},
                )
                return
            session.execute(
                text(
                    f"INSERT INTO {_PENDING_TABLE} "
                    "(prediction_id, idempotency_key, payload_json, attempts, status, last_error, created_ts, updated_ts, next_attempt_ts) "
                    "VALUES (:prediction_id, :key, :payload, :attempts, 'pending', :error, :created, :updated, :next)"
                ),
                {"prediction_id": prediction_id, "key": idempotency_key, "payload": encoded, "attempts": attempts, "error": error, "created": now, "updated": now, "next": next_attempt},
            )

    def due(self, *, now: int, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_schema()
        with session_scope(self.database_url) as session:
            rows = session.execute(
                text(f"SELECT * FROM {_PENDING_TABLE} WHERE status = 'pending' AND next_attempt_ts <= :now ORDER BY id LIMIT :limit"),
                {"now": now, "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]

    def remove(self, row_id: int) -> None:
        self._ensure_schema()
        with session_scope(self.database_url) as session:
            session.execute(text(f"DELETE FROM {_PENDING_TABLE} WHERE id = :id"), {"id": row_id})

    def remove_by_key(self, idempotency_key: str) -> None:
        self._ensure_schema()
        with session_scope(self.database_url) as session:
            session.execute(text(f"DELETE FROM {_PENDING_TABLE} WHERE idempotency_key = :key"), {"key": idempotency_key})

    def reschedule(self, row_id: int, attempts: int, error: str, *, delay_s: float, status: str = "pending") -> None:
        self._ensure_schema()
        now = int(time.time() * 1000)
        with session_scope(self.database_url) as session:
            session.execute(
                text(
                    f"UPDATE {_PENDING_TABLE} SET attempts = :attempts, status = :status, last_error = :error, "
                    "updated_ts = :updated, next_attempt_ts = :next WHERE id = :id"
                ),
                {"attempts": attempts, "status": status, "error": error, "updated": now, "next": now + max(0, int(delay_s * 1000)), "id": row_id},
            )

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return queue state for operational status and user-facing sync feedback."""

        self._ensure_schema()
        where = "WHERE status = :status" if status is not None else ""
        params: dict[str, Any] = {"limit": max(1, min(500, int(limit)))}
        if status is not None:
            params["status"] = status
        with session_scope(self.database_url) as session:
            rows = session.execute(
                text(f"SELECT id, prediction_id, idempotency_key, attempts, status, last_error, created_ts, updated_ts, next_attempt_ts FROM {_PENDING_TABLE} {where} ORDER BY id DESC LIMIT :limit"),
                params,
            ).mappings().all()
        return [dict(row) for row in rows]


def _role_allows(role: str, required_role: str) -> bool:
    return role == required_role or role == "admin"


class ERPActionAgent:
    """The sole application component allowed to create ERP work orders."""

    name = "erp_action"
    version = "v1"
    model_name = "deterministic"
    provider = "local"

    def __init__(
        self,
        *,
        database_url: str | None = None,
        erp_client: ERPClient | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        max_attempts: int = 3,
        retry_delays_s: tuple[float, ...] = (0.1, 0.2),
        jitter_s: float = 0.05,
        pending_retry_delay_s: float = 1.0,
        pending_poll_s: float = 0.25,
        start_worker: bool = True,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if any(delay < 0 for delay in retry_delays_s) or jitter_s < 0 or pending_retry_delay_s < 0 or pending_poll_s <= 0:
            raise ValueError("ERP retry delays must be non-negative and polling must be positive")
        self.database_url = database_url
        erp_config = settings.erp
        self.max_attempts = max_attempts
        self.retry_delays_s = retry_delays_s
        self.jitter_s = jitter_s
        self.pending_retry_delay_s = pending_retry_delay_s
        self.pending_poll_s = pending_poll_s
        self.client: ERPClient = erp_client or HTTPERPClient(
            base_url=base_url or erp_config.base_url,
            api_key=api_key or erp_config.api_key or _DEFAULT_ERP_API_KEY,
            timeout_s=timeout_s or erp_config.timeout_s,
        )
        self._pending = _PendingSyncStore(database_url)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._client_lock = threading.Lock()
        self._mirror_lock = threading.Lock()
        self._last_full_mirror_at = 0.0
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._drain_loop, name="erp-pending-sync", daemon=True)
            self._worker.start()

    def _drain_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self.pending_poll_s)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.drain_pending()
            except Exception:  # pragma: no cover - defensive worker boundary
                logger.exception("pending ERP sync worker failed")

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout=2)
        if hasattr(self.client, "close"):
            self.client.close()  # type: ignore[attr-defined]

    def _approval(self, prediction_id: str, approval_id: str | int | None, actor: str | None, role: str | None) -> Approval | None:
        create_schema(self.database_url)
        with session_scope(self.database_url) as session:
            approval: Approval | None = None
            if approval_id is not None:
                numeric_id = str(approval_id).removeprefix("approval-")
                if not numeric_id.isdigit():
                    return None
                approval = session.get(Approval, int(numeric_id))
            else:
                approval = session.scalars(
                    select(Approval).where(Approval.prediction_id == prediction_id).order_by(Approval.id.desc())
                ).first()
            if (
                approval is None
                or approval.prediction_id != prediction_id
                or approval.state != "approved"
                or approval.autonomy_level != "A0"
                or not approval.actor
                or not approval.action_payload
                or not approval.payload_hash
            ):
                return None
            user = session.get(User, actor or approval.actor)
            if user is None:
                return None
            user_role = str(user.role)
            if role is not None and role != user_role:
                return None
            if not _role_allows(user_role, str(approval.required_role)):
                return None
            return approval

    def _blocked(self, *, prediction_id: str, approval_id: str | int | None, actor: str | None, reason: str) -> None:
        message = "blocked unauthorised ERP tool execution"
        logger.warning(
            message,
            extra={
                "event": "blocked_unauthorised_tool_execution",
                "prediction_id": prediction_id,
                "approval_id": approval_id,
                "actor": actor,
                "reason": reason,
            },
        )
        try:
            _append_audit_event(
                action="blocked_unauthorised_tool_execution",
                subject=prediction_id,
                payload={"approval_id": approval_id, "actor": actor, "reason": reason},
                actor=actor or "erp_action",
                database_url=self.database_url,
            )
        except Exception:  # pragma: no cover - audit failure must not hide the security decision
            logger.exception("could not persist blocked ERP tool execution audit event")

    def _erp_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        asset_id = str(payload.get("asset_id") or payload.get("machine_id") or "")
        if not asset_id:
            raise ValueError("ERP work-order payload requires asset_id or machine_id")
        description = str(payload.get("description") or payload.get("action") or "Predictive maintenance work order")
        fault_code = str(payload.get("fault_code") or payload.get("mode") or "PREDICTED-FAULT")
        result = {
            "asset_id": asset_id,
            "fault_code": fault_code,
            "description": description,
            "priority": str(payload.get("priority") or "high"),
            "scheduled_start": payload.get("scheduled_start"),
            "technician_notes": str(payload.get("technician_notes") or "Created from an approved predictive-maintenance recommendation."),
            "parts": list(payload.get("parts") or []),
            "labour_hours": float(payload.get("labour_hours", payload.get("est_labour_hours", 0)) or 0),
            "downtime_minutes": int(payload.get("downtime_minutes", 0) or 0),
            "cost_gbp": float(payload.get("cost_gbp", 0) or 0),
        }
        return _json_ready(result)

    def _request_with_retries(self, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        last_error: BaseException | None = None
        for attempt in range(self.max_attempts):
            try:
                with self._client_lock:
                    return self.client.create_work_order(payload, idempotency_key)
            except ERPClientError:
                raise
            except (ERPTransientError, httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    break
                delay = self.retry_delays_s[min(attempt, len(self.retry_delays_s) - 1)] if self.retry_delays_s else 0
                if delay:
                    time.sleep(delay + random.uniform(0, self.jitter_s))
        raise ERPTransientError(str(last_error or "ERP request failed")) from last_error

    @staticmethod
    def _timestamp_ms(value: Any) -> int | None:
        if value in {None, ""}:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)

    def _mirror_values(self, result: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
        required = {"workorder_id", "asset_id", "fault_code", "created_at"}
        if not required.issubset(result):
            return None
        parts = result.get("parts") or []
        normalized_parts = [part if isinstance(part, Mapping) else {"name": str(part)} for part in parts]
        created_ts = self._timestamp_ms(result.get("created_at"))
        if created_ts is None:
            return None
        return str(result["workorder_id"]), {
            "machine_id": str(result["asset_id"]),
            "fault_code": str(result["fault_code"]),
            "technician_notes": str(result.get("technician_notes") or result.get("description") or ""),
            "parts_used": normalized_parts,
            "labour_hours": float(result.get("labour_hours") or 0),
            "downtime_minutes": int(result.get("downtime_minutes") or 0),
            "cost_gbp": float(result.get("cost_gbp") or 0),
            "lead_time_days": int(result.get("lead_time_days") or 0),
            "order_type": str(result.get("order_type") or "corrective"),
            "status": str(result.get("status") or "open"),
            "created_ts": created_ts,
            "completed_ts": self._timestamp_ms(result.get("completed_at")),
        }

    def _mirror_work_order(self, result: Mapping[str, Any]) -> None:
        """Upsert a successful ERP response into the offline plant mirror."""

        normalized = self._mirror_values(result)
        if normalized is None:
            return
        workorder_id, values = normalized
        with session_scope(self.database_url) as session:
            work_order = session.get(WorkOrder, workorder_id)
            if work_order is None:
                session.add(WorkOrder(workorder_id=workorder_id, **values))
            else:
                for key, value in values.items():
                    setattr(work_order, key, value)

    def mirror_work_orders(self, rows: list[Mapping[str, Any]]) -> int:
        """Refresh the offline mirror from authoritative live ERP history."""

        normalized_rows = [
            {"workorder_id": workorder_id, **values}
            for row in rows
            if (normalized := self._mirror_values(row)) is not None
            for workorder_id, values in (normalized,)
        ]
        if not normalized_rows:
            return 0
        with self._mirror_lock:
            now = time.monotonic()
            if now - self._last_full_mirror_at < 5.0:
                return 0
            with session_scope(self.database_url) as session:
                for start in range(0, len(normalized_rows), 200):
                    statement = sqlite_insert(WorkOrder).values(normalized_rows[start:start + 200])
                    update_fields = {
                        column.name: statement.excluded[column.name]
                        for column in WorkOrder.__table__.columns
                        if column.name != "workorder_id"
                    }
                    session.execute(statement.on_conflict_do_update(index_elements=["workorder_id"], set_=update_fields))
            self._last_full_mirror_at = now
        return len(normalized_rows)

    def pending_syncs(self, *, include_failed: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._pending.list(limit=limit)
        return rows if include_failed else [row for row in rows if row["status"] == "pending"]

    def _record_approval_sync(self, approval_id: int, status: str, workorder_id: Any = None) -> None:
        with session_scope(self.database_url) as session:
            approval = session.get(Approval, approval_id)
            if approval is None:
                return
            approval.erp_sync_status = status
            if workorder_id not in {None, ""}:
                approval.erp_workorder_id = str(workorder_id)

    def _approval_id_for_idempotency(self, prediction_id: str, idempotency_key: str) -> int | None:
        with session_scope(self.database_url) as session:
            approvals = session.scalars(
                select(Approval).where(
                    Approval.prediction_id == prediction_id,
                    Approval.state == "approved",
                    Approval.autonomy_level == "A0",
                    Approval.payload_hash.is_not(None),
                )
            ).all()
        for approval in approvals:
            expected = hashlib.sha256(f"approval-{approval.id}:{approval.payload_hash}".encode("utf-8")).hexdigest()
            if expected == idempotency_key:
                return int(approval.id)
        return None

    def create_work_order(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Create exactly one idempotent ERP work order after approval."""

        values = dict(payload)
        prediction_id = str(values.get("prediction_id") or "")
        approval_id = values.get("approval_id")
        if not prediction_id:
            raise ValueError("ERP work-order payload requires prediction_id")
        approval = self._approval(prediction_id, approval_id, actor, role)
        if approval is None:
            reason = "no approved approval row with a role-authorised actor"
            self._blocked(prediction_id=prediction_id, approval_id=approval_id, actor=actor, reason=reason)
            raise PermissionError(reason)

        submitted_payload = {key: value for key, value in values.items() if key not in {"approval_id", "payload_hash"}}
        canonical = json.dumps(submitted_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        submitted_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if submitted_hash != approval.payload_hash or values.get("payload_hash") != approval.payload_hash:
            reason = "submitted ERP payload does not match the immutable approved payload"
            self._blocked(prediction_id=prediction_id, approval_id=approval_id, actor=actor, reason=reason)
            raise PermissionError(reason)

        with session_scope(self.database_url) as session:
            prediction = session.get(Prediction, prediction_id)
            expected_asset = str(prediction.machine_id).upper() if prediction is not None else ""
        requested_asset = str(values.get("asset_id") or values.get("machine_id") or "").upper()
        if expected_asset and requested_asset != expected_asset:
            reason = f"approved prediction belongs to {expected_asset}, not {requested_asset or 'an unspecified asset'}"
            self._blocked(prediction_id=prediction_id, approval_id=approval_id, actor=actor, reason=reason)
            raise PermissionError(reason)

        idempotency_key = hashlib.sha256(f"approval-{approval.id}:{approval.payload_hash}".encode("utf-8")).hexdigest()
        erp_payload = self._erp_payload(values)
        try:
            result = self._request_with_retries(erp_payload, idempotency_key)
        except ERPClientError:
            self._record_approval_sync(int(approval.id), "failed")
            raise
        except ERPTransientError as exc:
            self._pending.enqueue(prediction_id, idempotency_key, erp_payload, str(exc), attempts=self.max_attempts, delay_s=self.pending_retry_delay_s)
            self._record_approval_sync(int(approval.id), "pending_sync")
            self._wake.set()
            logger.warning("ERP write queued for pending sync", extra={"prediction_id": prediction_id, "idempotency_key": idempotency_key})
            return {"status": "pending_sync", "prediction_id": prediction_id, "idempotency_key": idempotency_key, "error": str(exc)}

        self._pending.remove_by_key(idempotency_key)
        self._mirror_work_order(result)
        self._record_approval_sync(int(approval.id), "synced", result.get("workorder_id"))
        _append_audit_event(
            action="erp_write",
            subject=prediction_id,
            payload={"idempotency_key": idempotency_key, "workorder_id": result.get("workorder_id")},
            actor=actor or "erp_action",
            database_url=self.database_url,
        )
        return result

    def drain_pending(self, *, limit: int = 50) -> int:
        """Attempt due queued writes and return the number successfully drained."""

        drained = 0
        for row in self._pending.due(now=int(time.time() * 1000), limit=limit):
            payload = json.loads(str(row["payload_json"]))
            approval_id = self._approval_id_for_idempotency(str(row["prediction_id"]), str(row["idempotency_key"]))
            try:
                result = self._request_with_retries(payload, str(row["idempotency_key"]))
            except ERPClientError as exc:
                self._pending.reschedule(int(row["id"]), int(row["attempts"]) + 1, str(exc), delay_s=self.pending_retry_delay_s, status="failed")
                if approval_id is not None:
                    self._record_approval_sync(approval_id, "failed")
                continue
            except ERPTransientError as exc:
                self._pending.reschedule(int(row["id"]), int(row["attempts"]) + self.max_attempts, str(exc), delay_s=self.pending_retry_delay_s, status="pending")
                if approval_id is not None:
                    self._record_approval_sync(approval_id, "pending_sync")
                continue
            self._pending.remove(int(row["id"]))
            self._mirror_work_order(result)
            if approval_id is not None:
                self._record_approval_sync(approval_id, "synced", result.get("workorder_id"))
            drained += 1
            _append_audit_event(
                action="erp_write",
                subject=str(row["prediction_id"]),
                payload={"idempotency_key": row["idempotency_key"], "workorder_id": result.get("workorder_id"), "drained": True},
                actor="erp_action",
                database_url=self.database_url,
            )
        return drained

    def run(self, context: RunContext) -> AgentResult:
        """Agent-protocol adapter for orchestrator integrations."""

        started = time.perf_counter()
        try:
            output = self.create_work_order(context.payload, actor=str(context.payload.get("actor")) if context.payload.get("actor") else None, role=str(context.payload.get("role")) if context.payload.get("role") else None)
            return AgentResult(ok=True, output=output, latency_ms=(time.perf_counter() - started) * 1000, model_name=self.model_name, provider=self.provider)
        except PermissionError as exc:
            return AgentResult(ok=False, output={}, errors=[str(exc)], latency_ms=(time.perf_counter() - started) * 1000, model_name=self.model_name, provider=self.provider)
        except ERPActionError as exc:
            return AgentResult(ok=False, output={}, errors=[str(exc)], latency_ms=(time.perf_counter() - started) * 1000, model_name=self.model_name, provider=self.provider)
