"""FastAPI application exposing the Appendix-B contract."""

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence
from uuid import uuid4

import httpx
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agents.base import AgentResult, RunContext, load_agent_runs
from agents.coordinator_agent import CoordinatorAgent
from agents.guardrail_agent import GuardrailAgent
from agents.maintenance_log_agent import MaintenanceLogAgent
from agents.orchestrator import DiagnosisRun, DiagnosisRunResult
from agents.sensor_agent import PlantTSDBClient, SensorAgent
from api.auth import (
    COOKIE_NAME,
    TOKEN_TTL,
    Principal,
    authenticate_user,
    create_access_token,
    require_authenticated,
    require_role,
)
from agents.erp_action_agent import ERPActionAgent, ERPClientError
from api.schemas import (
    Alert,
    AlertPatch,
    AlertState,
    Approval,
    ApprovalDecision,
    ApprovalState,
    AuditEntry,
    AuditVerifyResponse,
    Band,
    Citation,
    ComponentStatus,
    CurrentUserResponse,
    DependencyStatus,
    DiagnoseRequest,
    DiagnoseResponse,
    DiagnosisResponse,
    DlqItem,
    ErrorResponse,
    EvalMetric,
    EvalReportResponse,
    ERPActionResponse,
    ERPActionPayload,
    ERPApprovalExecute,
    ERPAsset,
    ERPPart,
    ERPPartList,
    ERPStatus,
    ERPSyncItem,
    ERPWorkOrder,
    ERPWorkOrderList,
    FaultEventSummary,
    FeatureContribution,
    HealthResponse,
    IngestBatchRequest,
    IngestBatchResponse,
    LedgerMissedFailure,
    LedgerPolicy,
    LedgerResponse,
    LlmOfflineResponse,
    LlmOfflineToggleRequest,
    LoginRequest,
    LoginResponse,
    MachineDetail,
    MachineSummary,
    MessageResponse,
    MetaResponse,
    MetricsResponse,
    PredictionResponse,
    PersonaSwitchRequest,
    QuarantineEntry,
    QuarantineReleaseRequest,
    QuarantineReleaseResponse,
    RehearsalInjectRequest,
    RehearsalInjectResponse,
    RehearsalResetResponse,
    RehearsalScoreboardResponse,
    RecommendedAction,
    RejectRequest,
    RulInterval,
    RunResponse,
    AgentRun as AgentRunResponse,
    ScheduleWindow,
    ScoreboardItem,
    TelemetryPoint,
    TelemetryResponse,
    WorkOrderSummary,
)
from core.config import settings
from core.audit import append_event, verify_ledger
from core.currency import gbp_to_inr, gbp_to_inr_whole, inr_to_gbp
from core.downtime_sim import DowntimeSimulationResult, simulate_downtime
from core.quarantine import list_quarantines, release_quarantine
from core.ingest import AlertGenerator, EventBroker, ReplayManager, StreamClock, delete_simulated_alerts, delete_simulated_points, latest_complete_timestamp, process_batch, read_dlq, read_ingest_points, read_parquet_points
from core.metrics import MetricsRegistry
from core.predict import Predictor, load_predictor
from core.db import create_schema, session_scope
from core.models import Approval as ApprovalRow, Diagnosis as DiagnosisRow, Prediction as PredictionRow
from core.rehearsal import MECHANICAL_MODES, build_rehearsal_point, failure_horizon, risk_delta
from core.runtime_paths import runtime_paths
from llm.tls import gemini_verify_setting
from api.security import RequestBodyTooLarge, SECURITY_HEADERS, TokenBucketRateLimiter, body_limit_for_path, rate_limit_for_path, request_identity


logger = logging.getLogger(__name__)


app = FastAPI(
    title="SentinelPdM API",
    version="0.1.0",
    description="Appendix-B API for the predictive-maintenance demo.",
    redoc_url=None,
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Requested-With"],
)


@app.on_event("startup")
def validate_database_schema() -> None:
    create_schema(settings.db_url)

RATE_LIMITER = TokenBucketRateLimiter()
RUNTIME_METRICS = MetricsRegistry()


def _response_error_code(response: Response) -> str | None:
    """Extract the frozen API error code when the response has a JSON body."""

    if response.status_code < 400:
        return None
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError):
            return None
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return str(code) if code else None
    return None


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    trace_id = uuid4().hex
    request.state.trace_id = trace_id
    body_limit = body_limit_for_path(request.url.path)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            response = _error("invalid_content_length", "Invalid Content-Length header", trace_id, status.HTTP_400_BAD_REQUEST)
        else:
            response = None
            if declared_length < 0 or declared_length > body_limit:
                response = _error("request_too_large", "Request body exceeds the permitted size", trace_id, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if response is not None:
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
            RUNTIME_METRICS.record_request(response.status_code, _response_error_code(response))
            return response

    policy = rate_limit_for_path(request.url.path)
    if not RATE_LIMITER.allow(request_identity(request), policy):
        response = _error("rate_limited", "Request rate limit exceeded", trace_id, status.HTTP_429_TOO_MANY_REQUESTS)
        response.headers["Retry-After"] = "60"
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        RUNTIME_METRICS.record_request(response.status_code, _response_error_code(response))
        return response

    original_receive = request._receive
    received_bytes = 0

    async def limited_receive():
        nonlocal received_bytes
        message = await original_receive()
        if message.get("type") == "http.request":
            received_bytes += len(message.get("body", b""))
            if received_bytes > body_limit:
                raise RequestBodyTooLarge(body_limit)
        return message

    request._receive = limited_receive
    try:
        response = await call_next(request)
    except RequestBodyTooLarge:
        response = _error("request_too_large", "Request body exceeds the permitted size", trace_id, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    except Exception as exc:
        # This middleware intentionally returns a safe error envelope, but
        # suppressing the exception altogether makes operational failures in
        # agent and ERP workflows impossible to diagnose.
        logger.exception("request pipeline failed trace_id=%s", trace_id, exc_info=exc)
        response = _error("internal_error", "Internal server error", trace_id, status.HTTP_500_INTERNAL_SERVER_ERROR)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    RUNTIME_METRICS.record_request(response.status_code, _response_error_code(response))
    return response

def _utcnow() -> datetime:
    """Single injectable wall-clock seam for operational timestamps."""

    return datetime.now(UTC)


CHANNELS = [
    "vibration_rms_mm_s",
    "vibration_kurtosis",
    "temp_bearing_c",
    "temp_motor_c",
    "pressure_bar",
    "current_a",
    "rpm",
    "ambient_temp_c",
]
MODES = [
    "bearing_wear",
    "rotor_imbalance",
    "seal_leak",
    "motor_overheat",
    "lubrication_degradation",
    "sensor_drift",
]
MACHINE_IDS = ["PUMP-01", "PUMP-02", "PUMP-03", "PUMP-04", "PUMP-05", "PUMP-06", "PUMP-07", "PRESS-01", "PRESS-02", "PRESS-03", "CNC-09", "CNC-10", "CNC-11", "CONV-13", "CONV-14"]
def _configured_sqlite_path() -> Path:
    prefix = "sqlite:///"
    if not settings.db_url.startswith(prefix) or ":memory:" in settings.db_url:
        raise RuntimeError("Sentinel's plant repository currently requires a file-backed SQLite DB_URL")
    raw = settings.db_url.removeprefix(prefix)
    return Path(raw) if raw.startswith("/") else Path.cwd() / raw


ERP_ACTION_AGENT = ERPActionAgent(database_url=settings.db_url)


def _machine(index: int) -> MachineSummary:
    machine_id = MACHINE_IDS[index]
    machine_type = {"PUMP": "centrifugal pump", "PRESS": "hydraulic press", "CNC": "CNC mill", "CONV": "conveyor drive"}[machine_id.split("-", maxsplit=1)[0]]
    risk = float([18, 27, 39, 58, 74][index % 5])
    band = "CRITICAL" if risk >= 75 else "HIGH" if risk >= 55 else "MEDIUM" if risk >= 35 else "LOW"
    return MachineSummary(
        id=machine_id,
        name=f"{machine_id} production asset",
        type=machine_type,
        model=f"SENTINEL-{100 + index}",
        line=f"LINE-{index % 3 + 1}",
        criticality=(index % 5) + 1,
        risk=risk,
        band=band,
        status="degraded" if risk >= 55 else "online",
        quality=0.98 if index % 6 else 0.91,
        p_failure_24h=round(risk / 125, 3),
        rul_days=round(max(1.5, 14 - risk / 8), 1),
        updated_at=_utcnow(),
    )


def _load_machines() -> list[MachineSummary]:
    database_path = _configured_sqlite_path()
    if not database_path.exists():
        return []
    try:
        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                """
                SELECT m.id, m.name, m.type, m.model, m.line, m.criticality,
                       COUNT(f.id) AS fault_count, MAX(f.failure_ts) AS latest_failure_ts
                FROM machines AS m
                LEFT JOIN fault_events AS f ON f.machine_id = m.id
                GROUP BY m.id, m.name, m.type, m.model, m.line, m.criticality
                ORDER BY m.id
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    machines: list[MachineSummary] = []
    for row in rows:
        risk = float(min(100, 12 + row[5] * 10 + row[6] * 2 + (8 if row[0] == "PUMP-07" else 0)))
        band = "CRITICAL" if risk >= 75 else "HIGH" if risk >= 55 else "MEDIUM" if risk >= 35 else "LOW"
        machines.append(
            MachineSummary(
                id=row[0], name=row[1], type=row[2], model=row[3], line=row[4], criticality=row[5], risk=risk,
                band=band, status="degraded" if risk >= 55 else "online", quality=0.91 if row[0] == "CONV-14" else 0.98,
                p_failure_24h=round(min(1.0, risk / 125), 3), rul_days=round(max(1.5, 14 - risk / 8), 1),
                updated_at=datetime.fromtimestamp(row[7] / 1000, tz=UTC) if row[7] else _utcnow(),
            )
        )
    return machines


MACHINES = _load_machines()
_REHEARSAL_BASELINE = tuple(machine.model_copy(deep=True) for machine in MACHINES)
BROKER = EventBroker()
REPLAY_MANAGER: ReplayManager | None = None
REPLAY_SPEED = float(settings.stream.speedup)
ALERT_GENERATOR = AlertGenerator()
_DEMO_CROSSING_RE = re.compile(r"PUMP-07 fused risk crossed tau=[0-9.]+ at ([0-9.]+)s")


def _demo_alert_cue_seconds() -> float:
    """Return the latest measured PUMP-07 cue, with a safe config fallback."""

    lower, upper = (float(value) for value in settings.stream.demo_alert_window_s)
    notes_path = runtime_paths.data_root / "DEMO_NOTES.md"
    try:
        match = _DEMO_CROSSING_RE.search(notes_path.read_text(encoding="utf-8"))
    except OSError:
        match = None
    if match is None:
        return lower
    measured = float(match.group(1))
    return measured if lower <= measured <= upper else lower
@dataclass(frozen=True)
class RehearsalRecord:
    event_id: str
    machine_id: str
    mode: str
    severity: str
    onset: datetime
    detected: bool
    observed_mode: str | None
    mode_correct: bool
    lead_time_hours: float | None
    alert_ids: tuple[str, ...]


REHEARSAL_EVENTS: dict[str, RehearsalRecord] = {}
_REHEARSAL_LOCK = Lock()
LLM_OFFLINE_OVERRIDE = False
_LLM_OFFLINE_LOCK = Lock()
_DIAGNOSIS_CACHE_TTL_S = 15 * 60


@dataclass(frozen=True)
class CachedDiagnosis:
    expires_at: float
    response: DiagnoseResponse


_DIAGNOSIS_CACHE: dict[tuple[str, bool, str], CachedDiagnosis] = {}
_DIAGNOSIS_CACHE_LOCK = Lock()
_PREDICTOR: Predictor | None = None
HEALTH_DEPENDENCY_NAMES = ("db", "tsdb", "models", "erp", "llm", "stream")
HEALTH_PROBE_TIMEOUT_S = 0.5
_HEALTH_BREAKER_STATES: dict[str, str] = {name: "closed" for name in HEALTH_DEPENDENCY_NAMES}
_HEALTH_BREAKER_LOCK = Lock()


def _error(code: str, message: str, trace_id: str | None = None, http_status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"error": {"code": code, "message": message, "trace_id": trace_id or uuid4().hex}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
    return _error(detail.get("code", "http_error"), detail.get("message", "Request failed"), detail.get("trace_id"), exc.status_code)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return _error("validation_error", f"Invalid request field: {location}", http_status=status.HTTP_422_UNPROCESSABLE_ENTITY)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", uuid4().hex)
    logger.exception("unhandled request error trace_id=%s", trace_id, exc_info=exc)
    return _error("internal_error", "Internal server error", trace_id, status.HTTP_500_INTERNAL_SERVER_ERROR)


def _find_machine(machine_id: str) -> MachineSummary:
    for machine in MACHINES:
        if machine.id == machine_id:
            return machine
    raise HTTPException(status_code=404, detail={"code": "machine_not_found", "message": f"Unknown machine: {machine_id}"})


def _db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(_configured_sqlite_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _work_orders(machine_id: str) -> list[WorkOrderSummary]:
    database_path = _configured_sqlite_path()
    if not database_path.exists():
        return []
    try:
        with _db_connection() as connection:
            rows = connection.execute(
                "SELECT workorder_id, machine_id, fault_code, status, created_ts, completed_ts, technician_notes FROM work_orders WHERE machine_id = ? ORDER BY created_ts DESC",
                (machine_id,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        WorkOrderSummary(
            workorder_id=row["workorder_id"], machine_id=row["machine_id"], fault_code=row["fault_code"],
            description=row["technician_notes"], status=row["status"] if row["status"] in {"open", "in_progress", "completed", "cancelled"} else "completed",
            created_at=datetime.fromtimestamp(row["created_ts"] / 1000, tz=UTC),
            completed_at=datetime.fromtimestamp(row["completed_ts"] / 1000, tz=UTC) if row["completed_ts"] is not None else None,
        )
        for row in rows
    ]


def _machine_metadata(machine_id: str) -> tuple[str, int, int] | None:
    try:
        with _db_connection() as connection:
            row = connection.execute("SELECT install_date, rated_rpm, hourly_downtime_cost_gbp FROM machines WHERE id = ?", (machine_id,)).fetchone()
    except sqlite3.Error:
        return None
    return (datetime.fromtimestamp(row["install_date"] / 1000, tz=UTC).date().isoformat(), row["rated_rpm"], row["hourly_downtime_cost_gbp"]) if row else None


def _component_band(risk: float) -> Band:
    if risk >= 75:
        return "CRITICAL"
    if risk >= 55:
        return "HIGH"
    if risk >= 35:
        return "MEDIUM"
    return "LOW"


def _rehearsal_update_machine(machine_id: str, delta: float, updated_at: datetime) -> None:
    with _REHEARSAL_LOCK:
        for index, machine in enumerate(MACHINES):
            if machine.id != machine_id:
                continue
            risk = min(100.0, max(0.0, machine.risk + delta))
            band = _component_band(risk)
            MACHINES[index] = machine.model_copy(
                update={
                    "risk": risk,
                    "band": band,
                    "status": "degraded" if risk >= 55 else "online",
                    "p_failure_24h": round(min(1.0, risk / 125.0), 3),
                    "rul_days": round(max(1.5, 14.0 - risk / 8.0), 1),
                    "updated_at": updated_at,
                }
            )
            return


def _rehearsal_restore_machines() -> int:
    with _REHEARSAL_LOCK:
        MACHINES[:] = [machine.model_copy(deep=True) for machine in _REHEARSAL_BASELINE]
        return len(MACHINES)


COMPONENT_ROSTERS: dict[str, tuple[tuple[str, str, float, bool], ...]] = {
    "industrial_pump": (
        ("bearing", "vibration_rms_mm_s", 1.00, True),
        ("shaft", "rpm", 0.72, False),
        ("seal", "pressure_bar", 0.48, False),
        ("motor housing", "temp_motor_c", 0.66, False),
        ("impeller", "current_a", 0.82, False),
    ),
    "hydraulic_press": (
        ("pump bearing", "vibration_rms_mm_s", 1.00, True),
        ("drive shaft", "rpm", 0.72, False),
        ("cylinder seal", "pressure_bar", 0.48, False),
        ("motor housing", "temp_motor_c", 0.66, False),
        ("hydraulic pump", "current_a", 0.82, False),
    ),
    "cnc_mill": (
        ("spindle bearing", "vibration_rms_mm_s", 1.00, True),
        ("spindle shaft", "rpm", 0.72, False),
        ("coolant seal", "pressure_bar", 0.48, False),
        ("spindle motor", "temp_motor_c", 0.66, False),
        ("tool holder", "current_a", 0.82, False),
    ),
    "conveyor_drive": (
        ("pulley bearing", "vibration_rms_mm_s", 1.00, True),
        ("drive shaft", "rpm", 0.72, False),
        ("gearbox seal", "pressure_bar", 0.48, False),
        ("drive motor", "temp_motor_c", 0.66, False),
        ("drive pulley", "current_a", 0.82, False),
    ),
}


def _machine_components(machine: MachineSummary) -> list[ComponentStatus]:
    """Five risk-bearing components per asset, named for the machine's own anatomy.

    Slot order is fixed across every roster — bearing, shaft, seal, motor, driven element — so the
    UI can map component index onto schematic geometry without matching on free-text names.
    """
    component_definitions = COMPONENT_ROSTERS.get(machine.type, COMPONENT_ROSTERS["industrial_pump"])
    components: list[ComponentStatus] = []
    for name, channel, factor, predicted in component_definitions:
        risk = round(max(0.0, min(100.0, machine.risk * factor)), 1)
        components.append(
            ComponentStatus(
                name=name,
                channel=channel,
                risk=risk,
                status=_component_band(risk),
                predicted=predicted,
            )
        )
    return components


def _fault_events(machine_id: str) -> list[FaultEventSummary]:
    """Read labelled events from the generated plant database.

    The API deliberately maps the persistence representation to a small,
    versioned response model so the UI never needs direct database access.
    Missing generated data is treated as an honest empty history for local
    development; it must not make unrelated machine-detail requests fail.
    """
    database_path = _configured_sqlite_path()
    if not database_path.exists():
        return []
    try:
        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                """
                SELECT id, machine_id, mode, onset_ts, failure_ts, severity, actual_downtime_min
                FROM fault_events
                WHERE machine_id = ?
                ORDER BY onset_ts
                """,
                (machine_id,),
            ).fetchall()
    except sqlite3.Error:
        return []

    def to_datetime(timestamp_ms: int | None) -> datetime | None:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC) if timestamp_ms is not None else None

    return [
        FaultEventSummary(
            event_id=row[0],
            machine_id=row[1],
            mode=row[2],
            onset_ts=to_datetime(row[3]),
            failure_ts=to_datetime(row[4]),
            severity=row[5],
            actual_downtime_min=row[6],
        )
        for row in rows
    ]


def _prediction_engine() -> Predictor:
    global _PREDICTOR
    if _PREDICTOR is None:
        _PREDICTOR = load_predictor()
    return _PREDICTOR


def _prediction_boundary(machine: MachineSummary, window_end_ms: int) -> PredictionResponse:
    """Describe the latest model/window identity without synthesising a score."""

    engine = _prediction_engine()
    active = getattr(engine, "primary", engine)
    model_version = str(getattr(active, "model_version", getattr(engine, "model_version", settings.features.version)))
    predictor_name = str(getattr(active, "predictor_name", getattr(engine, "predictor_name", "pending_sensor_score")))
    version_digest = hashlib.sha256(model_version.encode("utf-8")).hexdigest()[:8]
    return PredictionResponse(
        prediction_id=f"pred-{machine.id.lower()}-{window_end_ms}-{version_digest}",
        machine_id=machine.id,
        ts=datetime.fromtimestamp(window_end_ms / 1000, tz=UTC),
        p_failure_24h=0.0,
        rul=RulInterval(p10_days=0.0, p50_days=0.0, p90_days=0.0),
        mode_probs={"unknown": 1.0},
        top_features=[],
        risk=0.0,
        band="LOW",
        confidence=0.0,
        predictor=predictor_name,
        model_version=model_version,
    )


def _llm_offline_state() -> LlmOfflineResponse:
    """Return the effective presenter-controlled LLM mode.

    The toggle is process-local by design: it is a reversible chaos control for
    the demo, not a deployment configuration store.  A missing API key still
    makes the deterministic template path effective even when the override is
    switched off.
    """

    with _LLM_OFFLINE_LOCK:
        forced = LLM_OFFLINE_OVERRIDE
    effective = forced or not bool(settings.llm.api_key)
    return LlmOfflineResponse(
        enabled=forced,
        effective_offline=effective,
        mode="template" if effective else "gemini",
        model=str(settings.llm.models.coordinator.name),
        provider=str(settings.llm.provider),
    )


def _diagnosis_context(machine: MachineSummary) -> tuple[PredictionResponse, RunContext]:
    latest_ms = latest_complete_timestamp(machine.id, settings.db_url)
    if latest_ms is None:
        raise HTTPException(status_code=503, detail={"code": "telemetry_unavailable", "message": f"No complete telemetry window exists for {machine.id}"})
    sensor_end = datetime.fromtimestamp(latest_ms / 1000, tz=UTC)
    sensor_start = sensor_end - timedelta(hours=float(settings.features.window_hours))
    prediction = _prediction_boundary(machine, latest_ms)
    metadata = _machine_metadata(machine.id)

    context = RunContext(
        payload={
            "machine_id": machine.id,
            "t0": sensor_start,
            "t1": sensor_end,
            "channels": CHANNELS,
            "query": "fault history, repeat failures, effective repairs, parts, and downtime",
            "since": sensor_start - timedelta(days=365),
            "machine_meta": {
                "criticality": machine.criticality,
                "hourly_downtime_cost_gbp": float(metadata[2] if metadata else 1800.0),
            },
        }
    )
    return prediction, context


_CHANNEL_UNITS = {
    "vibration_rms_mm_s": "mm/s",
    "vibration_kurtosis": "",
    "temp_bearing_c": "°C",
    "temp_motor_c": "°C",
    "pressure_bar": "bar",
    "current_a": "A",
    "rpm": "rpm",
    "ambient_temp_c": "°C",
}


def _canonical_citation_id(value: Any) -> str:
    return str(value or "").strip().strip("[]").upper()


def _timestamp_from_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _clean_document(value: Any) -> str:
    return re.sub(r"</?untrusted_document\b[^>]*>", "", str(value or "")).strip()


def _citations_from_run(result: DiagnosisRunResult) -> list[Citation]:
    """Convert the exact Coordinator evidence set into public citations."""

    citations: list[Citation] = []
    seen: set[str] = set()
    evidence: Sequence[Any] = result.diagnosis_result.evidence or [
        *result.sensor_result.evidence,
        *result.maintenance_result.evidence,
    ]
    for raw in evidence:
        if not isinstance(raw, Mapping):
            continue
        identifier = _canonical_citation_id(raw.get("id", raw.get("citation_id")))
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        if identifier.startswith("S"):
            channel = str(raw.get("channel") or "") or None
            stat = str(raw.get("stat") or "window statistic")
            value = raw.get("value")
            unit = _CHANNEL_UNITS.get(channel or "", "")
            detail = str(raw.get("detail") or f"{stat}: {value}{f' {unit}' if unit else ''}")
            citations.append(
                Citation(
                    id=identifier,
                    kind="sensor",
                    title=f"{channel or 'Deterministic fusion'} evidence",
                    detail=detail,
                    channel=channel,
                    t0=_timestamp_from_ms(raw.get("t_start", raw.get("t0"))),
                    t1=_timestamp_from_ms(raw.get("t_end", raw.get("t1"))),
                )
            )
        elif identifier.startswith("M"):
            created_at = raw.get("created_at", raw.get("date"))
            try:
                date = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")) if created_at else None
            except ValueError:
                date = None
            fault_code = str(raw.get("fault_code") or "UNKNOWN")
            description = _clean_document(raw.get("description"))
            technician_note = _clean_document(raw.get("technician_notes", raw.get("technician_note")))
            citations.append(
                Citation(
                    id=identifier,
                    kind="work_order",
                    title=f"Prior {fault_code} work order",
                    detail=description or "Retrieved maintenance-history work order.",
                    workorder_id=str(raw.get("workorder_id") or "unknown"),
                    date=date,
                    fault_code=fault_code,
                    technician_note=technician_note or None,
                    cost_inr=gbp_to_inr(float(raw.get("cost_gbp") or 0)),
                )
            )
    return citations


def _prediction_from_sensor(
    machine: MachineSummary,
    fallback: PredictionResponse,
    sensor_result: AgentResult,
) -> PredictionResponse:
    output = sensor_result.output if isinstance(sensor_result.output, Mapping) else {}
    if not sensor_result.ok or output.get("refused") or not output.get("mode_probs"):
        return fallback
    mode_probs = {str(key): float(value) for key, value in dict(output["mode_probs"]).items()}
    p_failure = min(1.0, max(0.0, float(output.get("p_failure_24h", fallback.p_failure_24h))))
    risk = round(p_failure * 100.0, 2)
    quality = output.get("quality", {})
    confidence_value = output.get("confidence", quality.get("completeness", fallback.confidence)) if isinstance(quality, Mapping) else fallback.confidence
    end_values = [item.get("t_end") for item in sensor_result.evidence if isinstance(item, Mapping) and item.get("t_end") is not None]
    return PredictionResponse(
        prediction_id=str(output.get("prediction_id", fallback.prediction_id)),
        machine_id=machine.id,
        ts=(_timestamp_from_ms(max(end_values)) if end_values else None) or fallback.ts,
        p_failure_24h=p_failure,
        rul=RulInterval(
            p10_days=max(0.0, float(output.get("rul_p10", fallback.rul.p10_days))),
            p50_days=max(0.0, float(output.get("rul_p50", fallback.rul.p50_days))),
            p90_days=max(0.0, float(output.get("rul_p90", fallback.rul.p90_days))),
        ),
        mode_probs=mode_probs,
        top_features=[FeatureContribution.model_validate(item) for item in output.get("top_features", [])],
        risk=risk,
        band="CRITICAL" if risk >= 75 else "HIGH" if risk >= 55 else "MEDIUM" if risk >= 35 else "LOW",
        confidence=min(1.0, max(0.0, float(confidence_value))),
        predictor=str(output.get("predictor", fallback.predictor)),
        model_version=str(output.get("model_version", fallback.model_version)),
    )


def _diagnosis_from_agent(
    machine: MachineSummary,
    prediction: PredictionResponse,
    citations: list[Citation],
    result: AgentResult,
    *,
    offline_mode: bool,
) -> DiagnosisResponse:
    output = result.output
    actions = [RecommendedAction.model_validate(item) for item in output.get("recommended_actions", [])]
    fallback_reason = result.errors[0] if result.fallback_used and result.errors else ("offline_mode" if offline_mode else None)
    return DiagnosisResponse(
        diagnosis_id=f"diag-{uuid4().hex}",
        machine_id=machine.id,
        prediction_id=prediction.prediction_id,
        fault_mode=max(prediction.mode_probs, key=prediction.mode_probs.get),
        explanation=str(output.get("fault_diagnosis", "No diagnosis explanation was returned.")),
        confidence=float(output.get("confidence", prediction.confidence)),
        reasoning=[str(item) for item in output.get("reasoning_steps", [])],
        facts=[str(item) for item in output.get("facts", [])],
        assumptions=[str(item) for item in output.get("assumptions", [])],
        limits=[str(output.get("limits", "The diagnosis is limited to the supplied evidence."))],
        actions=[action.action for action in actions],
        recommended_actions=actions,
        risk=round(float(output.get("risk", prediction.risk)), 2),
        risk_terms={key: round(float(value), 2) for key, value in dict(output.get("fusion_terms", {})).items()},
        citations=citations,
        generated=bool(output.get("generated", False)),
        offline_mode=offline_mode,
        model=str(output.get("model", result.model_name)),
        provider=str(output.get("provider", result.provider)),
        prompt_version=str(output.get("prompt_version", "v1")),
        generated_at=output.get("generated_at", datetime.now(UTC)),
        fallback_reason=fallback_reason,
    )


async def _execute_diagnosis(
    machine: MachineSummary,
    *,
    trace_id: str,
    offline_mode: bool,
) -> tuple[DiagnosisResponse, DiagnosisRunResult]:
    """Execute the complete supervised agent chain for one machine."""

    fallback_prediction, context = _diagnosis_context(machine)
    context = RunContext(trace_id=trace_id, payload=context.payload)
    maintenance = MaintenanceLogAgent(
        # The measured Gemini latency exceeds the six-second budget. Keep
        # history deterministic so the Coordinator retains one reflection
        # retry without exceeding two LLM calls per diagnosis.
        llm_api_key="",
        database_url=settings.db_url,
    )
    coordinator = CoordinatorAgent(llm_api_key="" if offline_mode else None)
    run = DiagnosisRun(
        SensorAgent(
            PlantTSDBClient(database_url=settings.db_url),
            _prediction_engine(),
            database_url=settings.db_url,
        ),
        maintenance,
        coordinator,
        GuardrailAgent(database_url=settings.db_url),
        database_url=settings.db_url,
        event_sink=BROKER,
    )
    try:
        run_result = await run.execute(context)
    finally:
        maintenance.close()
        llm_client = getattr(coordinator, "llm_client", None)
        if hasattr(llm_client, "close"):
            llm_client.close()
    sensor_output = run_result.sensor_result.output if isinstance(run_result.sensor_result.output, Mapping) else {}
    if not run_result.sensor_result.ok or sensor_output.get("refused") or not sensor_output.get("mode_probs"):
        raise HTTPException(
            status_code=503,
            detail={"code": "prediction_unavailable", "message": "A prediction could not be produced from the latest complete telemetry window"},
        )
    prediction = _prediction_from_sensor(machine, fallback_prediction, run_result.sensor_result)
    diagnosis = _diagnosis_from_agent(
        machine,
        prediction,
        _citations_from_run(run_result),
        run_result.diagnosis_result,
        offline_mode=offline_mode,
    )
    return diagnosis, run_result


def _cached_diagnosis(key: tuple[str, bool, str]) -> DiagnoseResponse | None:
    now = time.monotonic()
    with _DIAGNOSIS_CACHE_LOCK:
        expired = [cache_key for cache_key, item in _DIAGNOSIS_CACHE.items() if item.expires_at <= now]
        for cache_key in expired:
            _DIAGNOSIS_CACHE.pop(cache_key, None)
        item = _DIAGNOSIS_CACHE.get(key)
        return item.response if item else None


def _cache_diagnosis(key: tuple[str, bool, str], response: DiagnoseResponse) -> None:
    with _DIAGNOSIS_CACHE_LOCK:
        _DIAGNOSIS_CACHE[key] = CachedDiagnosis(time.monotonic() + _DIAGNOSIS_CACHE_TTL_S, response)


def _persist_diagnosis(trace_id: str, diagnosis: DiagnosisResponse) -> None:
    with session_scope(settings.db_url) as session:
        session.add(
            DiagnosisRow(
                id=diagnosis.diagnosis_id,
                prediction_id=diagnosis.prediction_id,
                diagnosis=diagnosis.explanation,
                reasoning=list(diagnosis.reasoning),
                facts=list(diagnosis.facts),
                assumptions=list(diagnosis.assumptions),
                limits=list(diagnosis.limits),
                actions=list(diagnosis.actions),
                citations=[item.model_dump(mode="json") for item in diagnosis.citations],
                generated=diagnosis.generated,
                model=diagnosis.model,
                prompt_version=diagnosis.prompt_version,
                generated_at=int(diagnosis.generated_at.timestamp() * 1000),
                machine_id=diagnosis.machine_id,
                trace_id=trace_id,
                response_json=diagnosis.model_dump(mode="json"),
            )
        )


def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approval_action_payload(diagnosis: DiagnosisResponse) -> dict[str, Any]:
    action = diagnosis.recommended_actions[0] if diagnosis.recommended_actions else None
    historical = next((item for item in diagnosis.citations if item.kind == "work_order" and item.fault_code), None)
    events = _fault_events(diagnosis.machine_id)
    downtime = int(events[-1].actual_downtime_min) if events else 0
    payload = ERPActionPayload(
        prediction_id=diagnosis.prediction_id,
        asset_id=diagnosis.machine_id,
        fault_code=(historical.fault_code if historical and historical.fault_code else diagnosis.fault_mode.upper().replace("_", "-"))[:32],
        description=action.action if action else diagnosis.explanation[:500],
        priority=action.priority if action else "high",
        scheduled_start=None,
        technician_notes=action.rationale if action else "Created from the reviewed predictive-maintenance diagnosis.",
        parts=action.parts if action else [],
        labour_hours=action.est_labour_hours if action else 0,
        downtime_minutes=downtime,
        cost_gbp=inr_to_gbp(historical.cost_inr) if historical and historical.cost_inr is not None else 0,
    )
    return payload.model_dump(mode="json")


def _ensure_routed_approval(diagnosis: DiagnosisResponse, route: Mapping[str, Any]) -> int | None:
    """Materialize A0/A1 routing as one idempotent pending approval."""

    if route.get("action") not in {"planner_approval", "technician_ack"}:
        return None
    autonomy = str(route.get("autonomy") or "A0")
    required_role = str(route.get("required_role") or ("planner" if autonomy == "A0" else "technician"))
    request_key = f"{diagnosis.prediction_id}:{autonomy}:{required_role}"
    action_payload = _approval_action_payload(diagnosis) if autonomy == "A0" else None
    payload_hash = _canonical_payload_hash(action_payload) if action_payload is not None else None
    try:
        with _db_connection() as connection:
            existing = connection.execute(
                "SELECT id FROM approvals WHERE request_key = ? ORDER BY id DESC LIMIT 1",
                (request_key,),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = connection.execute(
                """INSERT INTO approvals
                   (prediction_id, autonomy_level, required_role, state, actor, reason, decided_at,
                    request_key, action_payload, payload_hash, erp_sync_status, erp_workorder_id)
                   VALUES (?, ?, ?, 'pending', NULL, NULL, NULL, ?, ?, ?, 'not_requested', NULL)""",
                (diagnosis.prediction_id, autonomy, required_role, request_key, json.dumps(action_payload) if action_payload is not None else None, payload_hash),
            )
            approval_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        with _db_connection() as connection:
            existing = connection.execute("SELECT id FROM approvals WHERE request_key = ?", (request_key,)).fetchone()
        if existing is None:
            raise
        return int(existing["id"])
    append_event(
        actor="orchestrator",
        action="approval_requested",
        subject=f"approval-{approval_id}",
        payload={
            "prediction_id": diagnosis.prediction_id,
            "autonomy_level": autonomy,
            "required_role": required_role,
            "payload_hash": payload_hash,
        },
        database_url=settings.db_url,
    )
    return approval_id


@app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(payload: LoginRequest, response: Response) -> LoginResponse:
    principal = authenticate_user(payload.username, payload.password)
    if principal is None:
        raise HTTPException(status_code=401, detail={"code": "invalid_credentials", "message": "Invalid username or password"})
    issued_at = datetime.now(UTC)
    response.set_cookie(
        COOKIE_NAME,
        create_access_token(principal.username, principal.role, now=issued_at),
        httponly=True,
        samesite="strict",
        max_age=int(TOKEN_TTL.total_seconds()),
        path="/",
    )
    return LoginResponse(username=principal.username, role=principal.role, expires_at=issued_at + TOKEN_TTL, can_switch_persona=principal.role == "admin")


@app.post("/auth/logout", response_model=MessageResponse, tags=["auth"], dependencies=[Depends(require_role("operator", "technician", "planner", "admin"))])
async def logout(response: Response) -> MessageResponse:
    response.delete_cookie(COOKIE_NAME, path="/")
    return MessageResponse(message="Session ended")


@app.get("/auth/me", response_model=CurrentUserResponse, tags=["auth"])
async def me(user: Principal = Depends(require_authenticated)) -> CurrentUserResponse:
    return CurrentUserResponse(username=user.username, role=user.role, can_switch_persona=user.can_switch_persona)


@app.post("/auth/persona", response_model=CurrentUserResponse, tags=["auth"])
async def switch_persona(
    payload: PersonaSwitchRequest,
    response: Response,
    user: Principal = Depends(require_authenticated),
) -> CurrentUserResponse:
    """Let an authenticated admin demo the product through a lower-role lens.

    The replacement session has the requested active role, so API enforcement
    remains real.  A signed, narrowly scoped claim lets the original admin
    switch back without exposing administrator actions in the lower persona.
    """

    if not user.can_switch_persona:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Administrator persona switching is required"})
    issued_at = datetime.now(UTC)
    response.set_cookie(
        COOKIE_NAME,
        create_access_token(user.username, payload.role, now=issued_at, persona_switcher=True),
        httponly=True,
        samesite="strict",
        max_age=int(TOKEN_TTL.total_seconds()),
        path="/",
    )
    append_event(
        actor=user.username,
        action="persona_switch",
        subject=f"persona:{payload.role}",
        payload={"from_role": user.role, "to_role": payload.role, "admin_session": True},
        database_url=settings.db_url,
    )
    return CurrentUserResponse(username=user.username, role=payload.role, can_switch_persona=True)


@app.get("/llm/offline", response_model=LlmOfflineResponse, tags=["system"], dependencies=[Depends(require_authenticated)])
async def llm_offline() -> LlmOfflineResponse:
    """Read the live LLM chaos-toggle state used by the Trust screen."""

    return _llm_offline_state()


@app.post("/llm/offline", response_model=LlmOfflineResponse, tags=["system"])
async def set_llm_offline(payload: LlmOfflineToggleRequest, _: Principal = Depends(require_role("admin"))) -> LlmOfflineResponse:
    """Force or release the deterministic template path for the current process."""

    global LLM_OFFLINE_OVERRIDE
    with _LLM_OFFLINE_LOCK:
        LLM_OFFLINE_OVERRIDE = payload.enabled
    return _llm_offline_state()


def _update_health_breaker(dependency: str, failed: bool) -> str:
    """Track a simple closed/open health breaker and count transitions."""

    with _HEALTH_BREAKER_LOCK:
        previous = _HEALTH_BREAKER_STATES.get(dependency, "closed")
        current = "open" if failed else "closed"
        if current == "open" and previous != "open":
            RUNTIME_METRICS.record_breaker_trip(dependency)
        _HEALTH_BREAKER_STATES[dependency] = current
        return current


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (time.perf_counter() - started) * 1000.0), 2)


def _exception_text(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def _check_database() -> tuple[str, float, str, str]:
    started = time.perf_counter()
    if not _configured_sqlite_path().exists():
        return "down", _elapsed_ms(started), "plant database file is unavailable", _update_health_breaker("db", True)
    try:
        with _db_connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        return "down", _elapsed_ms(started), f"plant database health check failed: {exc}", _update_health_breaker("db", True)
    return "up", _elapsed_ms(started), "ready", _update_health_breaker("db", False)


def _check_tsdb() -> tuple[str, float, str, str]:
    started = time.perf_counter()
    sensor_root = runtime_paths.sensor_root
    if not sensor_root.exists():
        return "down", _elapsed_ms(started), "time-series store is unavailable", "closed"
    if not any(sensor_root.rglob("*.parquet")):
        return "degraded", _elapsed_ms(started), "time-series store has no readable partitions", "closed"
    return "up", _elapsed_ms(started), "ready", "closed"


def _check_models() -> tuple[str, float, str, str]:
    started = time.perf_counter()
    manifest_path = runtime_paths.model_root / "MANIFEST.json"
    if not manifest_path.exists():
        return "down", _elapsed_ms(started), "model manifest is unavailable", "closed"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = manifest.get("artifacts", {})
        missing = [name for name in artifacts if not (manifest_path.parent / name).exists()]
    except (OSError, TypeError, ValueError) as exc:
        return "down", _elapsed_ms(started), f"model manifest could not be read: {exc}", "closed"
    if missing:
        return "degraded", _elapsed_ms(started), f"missing model artifacts: {', '.join(missing)}", "closed"
    return "up", _elapsed_ms(started), "ready", "closed"


async def _check_erp() -> tuple[str, float, str, str]:
    started = time.perf_counter()
    url = f"{settings.erp.base_url.rstrip('/')}/erp/v1/health"
    try:
        async with httpx.AsyncClient(timeout=HEALTH_PROBE_TIMEOUT_S) as client:
            response = await client.get(url)
        payload = response.json()
        erp_status = str(payload.get("status", "unknown")) if isinstance(payload, dict) else "unknown"
        healthy = response.status_code < 400 and erp_status == "healthy"
        status_value = "up" if healthy else "degraded"
        detail = f"ERP returned HTTP {response.status_code} ({erp_status})"
        return status_value, _elapsed_ms(started), detail, _update_health_breaker("erp", not healthy)
    except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
        return "degraded", _elapsed_ms(started), f"ERP health probe failed: {_exception_text(exc)}", _update_health_breaker("erp", True)


async def _check_llm() -> tuple[str, float, str, str, bool, bool, str, str]:
    started = time.perf_counter()
    tls_verified = bool(settings.llm.tls_verify)
    provider = str(settings.llm.provider)
    model_name = str(settings.llm.models.coordinator.name)
    base_detail = f"provider={provider}, model={model_name}"

    if not settings.llm.api_key:
        detail = f"{base_detail}; offline deterministic fallback active; Gemini API not probed"
        if not tls_verified:
            detail += "; tls_verified=false"
        # Offline mode is an intentional, supported operating mode.  The
        # local fallback remains operational, but the remote LLM dependency
        # is explicitly degraded so the health strip does not imply that the
        # Gemini API is reachable when no key is configured.
        return "degraded", _elapsed_ms(started), detail, _update_health_breaker("llm", False), False, tls_verified, model_name, provider

    try:
        verify_setting = gemini_verify_setting(tls_verify=tls_verified, cert_file=settings.ssl_cert_file)
        async with httpx.AsyncClient(timeout=HEALTH_PROBE_TIMEOUT_S, verify=verify_setting) as client:
            response = await client.get(
                f"{settings.llm.base_url.rstrip('/')}/models",
                headers={"x-goog-api-key": settings.llm.api_key},
            )
        reachable = response.status_code < 500
        model_available = False
        if 200 <= response.status_code < 400:
            payload = response.json()
            rows = payload.get("models", payload) if isinstance(payload, dict) else payload
            if isinstance(rows, list):
                model_ids = {
                    str(item.get("name") or item.get("id") or item.get("model_name")).removeprefix("models/")
                    for item in rows
                    if isinstance(item, dict) and "generateContent" in item.get("supportedGenerationMethods", ["generateContent"])
                }
                model_available = model_name in model_ids
        usable = 200 <= response.status_code < 400 and model_available and tls_verified
        detail = f"{base_detail}; Gemini model catalog HTTP {response.status_code}; model {'available' if model_available else 'unavailable'}"
        if not tls_verified:
            detail += "; tls_verified=false"
        return ("up" if usable else "degraded", _elapsed_ms(started), detail, _update_health_breaker("llm", not reachable), reachable, tls_verified, model_name, provider)
    except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
        detail = f"{base_detail}; Gemini API unreachable: {_exception_text(exc)}"
        if not tls_verified:
            detail += "; tls_verified=false"
        return "degraded", _elapsed_ms(started), detail, _update_health_breaker("llm", True), False, tls_verified, model_name, provider


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    db_status, db_latency, db_detail, db_breaker = _check_database()
    tsdb_status, tsdb_latency, tsdb_detail, tsdb_breaker = _check_tsdb()
    models_status, models_latency, models_detail, models_breaker = _check_models()
    erp_status, erp_latency, erp_detail, erp_breaker = await _check_erp()
    llm_status, llm_latency, llm_detail, llm_breaker, llm_reachable, tls_verified, model_name, provider = await _check_llm()
    stream_started = time.perf_counter()
    stream_status = "up" if BROKER is not None else "down"
    stream_detail = "broker ready" if stream_status == "up" else "event broker unavailable"
    stream_breaker = "closed"

    dependencies = [
        DependencyStatus(name="db", status=db_status, latency_ms=db_latency, detail=db_detail, breaker_state=db_breaker),
        DependencyStatus(name="tsdb", status=tsdb_status, latency_ms=tsdb_latency, detail=tsdb_detail, breaker_state=tsdb_breaker),
        DependencyStatus(name="models", status=models_status, latency_ms=models_latency, detail=models_detail, breaker_state=models_breaker),
        DependencyStatus(name="erp", status=erp_status, latency_ms=erp_latency, detail=erp_detail, breaker_state=erp_breaker),
        DependencyStatus(name="llm", status=llm_status, latency_ms=llm_latency, detail=llm_detail, breaker_state=llm_breaker, tls_verified=tls_verified, reachable=llm_reachable, model_name=model_name, provider=provider),
        DependencyStatus(name="stream", status=stream_status, latency_ms=round(max(0.0, (time.perf_counter() - stream_started) * 1000.0), 2), detail=stream_detail, breaker_state=stream_breaker),
    ]
    if any(item.status == "down" for item in dependencies):
        overall_status = "unhealthy"
    elif any(item.status == "degraded" for item in dependencies):
        overall_status = "degraded"
    else:
        overall_status = "healthy"
    replay = "stopped" if REPLAY_MANAGER is None else "paused" if REPLAY_MANAGER.clock.paused else "running"
    return HealthResponse(status=overall_status, dependencies=dependencies, replay=replay, model_version="fv1-demo-001", generated_at=datetime.now(UTC))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 2)


def _metrics_database() -> tuple[int, int, int, int, dict[str, list[float]], list[float], int, Counter[str], int, int, float]:
    """Read durable observations while tolerating an unavailable plant DB."""

    alerts_open = diagnoses_total = llm_calls = fallbacks = dlq_depth = 0
    agent_latencies: dict[str, list[float]] = defaultdict(list)
    prediction_latencies: list[float] = []
    guardrail_rejections: Counter[str] = Counter()
    prompt_tokens = completion_tokens = 0
    cost_gbp = 0.0
    try:
        with _db_connection() as connection:
            try:
                alerts_open = int(connection.execute("SELECT COUNT(*) FROM alerts WHERE state = 'open'").fetchone()[0])
            except sqlite3.Error:
                pass
            try:
                diagnoses_total = int(connection.execute("SELECT COUNT(*) FROM diagnoses").fetchone()[0])
            except sqlite3.Error:
                pass
            try:
                rows = connection.execute("SELECT agent, latency_ms, prompt_tokens, completion_tokens, cost_gbp, provider, fallback_used FROM agent_runs").fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                agent = str(row["agent"])
                latency = max(0.0, float(row["latency_ms"]))
                agent_latencies[agent].append(latency)
                if "predict" in agent.lower():
                    prediction_latencies.append(latency)
                provider = str(row["provider"])
                if provider != "local":
                    llm_calls += 1
                if bool(row["fallback_used"]):
                    fallbacks += 1
                prompt_tokens += max(0, int(row["prompt_tokens"]))
                completion_tokens += max(0, int(row["completion_tokens"]))
                cost_gbp += max(0.0, float(row["cost_gbp"]))
            try:
                dlq_depth = int(connection.execute("SELECT COUNT(*) FROM ingest_dlq").fetchone()[0])
            except sqlite3.Error:
                pass
            try:
                rows = connection.execute("SELECT subject, payload_json FROM audit_ledger WHERE action = 'guardrail_rejection'").fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                payload = row["payload_json"]
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except ValueError:
                        payload = {}
                rule = payload.get("rule") if isinstance(payload, dict) else None
                guardrail_rejections[str(rule or row["subject"])] += 1
    except sqlite3.Error:
        pass
    return (alerts_open, diagnoses_total, llm_calls, fallbacks, agent_latencies, prediction_latencies, dlq_depth, guardrail_rejections, prompt_tokens, completion_tokens, cost_gbp)


@app.get("/metrics", response_model=MetricsResponse, tags=["system"], dependencies=[Depends(require_authenticated)])
async def metrics() -> MetricsResponse:
    runtime = RUNTIME_METRICS.snapshot()
    alerts_open, diagnoses_total, llm_calls, fallbacks, agent_latencies, prediction_latencies, dlq_depth, guardrail_rejections, prompt_tokens, completion_tokens, cost_gbp = _metrics_database()
    latency_metrics = {
        agent: {"samples": len(values), "p50_ms": _percentile(values, 0.50), "p95_ms": _percentile(values, 0.95)}
        for agent, values in agent_latencies.items()
    }
    breaker_trips = {name: runtime.circuit_breaker_trips.get(name, 0) for name in HEALTH_DEPENDENCY_NAMES}
    return MetricsResponse(
        window="last_24h",
        requests=runtime.requests,
        errors_by_code=runtime.errors_by_code,
        agent_latencies=latency_metrics,
        guardrail_rejections_by_rule=dict(guardrail_rejections),
        llm_tokens={"prompt": prompt_tokens, "completion": completion_tokens, "cost_inr": gbp_to_inr(cost_gbp, decimal_places=6)},
        dlq_depth=dlq_depth,
        circuit_breaker_trips=breaker_trips,
        requests_total=runtime.requests,
        diagnoses_total=diagnoses_total,
        alerts_open=alerts_open,
        prediction_latency_p95_ms=_percentile(prediction_latencies, 0.95),
        llm_calls_total=llm_calls,
        llm_fallbacks_total=fallbacks,
    )


@app.get("/machines", response_model=list[MachineSummary], tags=["machines"], dependencies=[Depends(require_authenticated)])
async def machines() -> list[MachineSummary]:
    return MACHINES


@app.get("/machines/{machine_id}", response_model=MachineDetail, tags=["machines"], dependencies=[Depends(require_authenticated)])
async def machine_detail(machine_id: str) -> MachineDetail:
    machine = _find_machine(machine_id)
    fault_events = _fault_events(machine_id)
    metadata = _machine_metadata(machine_id)
    history = _work_orders(machine_id)
    if not history:
        work_order_created = fault_events[-1].onset_ts + timedelta(hours=2) if fault_events else _utcnow() - timedelta(hours=4)
        history = [WorkOrderSummary(workorder_id=f"WO-{machine_id}-001", machine_id=machine_id, fault_code="MECH-BRG", description="No maintenance history was returned.", status="completed", created_at=work_order_created, completed_at=work_order_created + timedelta(hours=2))]
    return MachineDetail(
        **machine.model_dump(),
        install_date=metadata[0] if metadata else "Unknown",
        rated_rpm=metadata[1] if metadata else 3600,
        hourly_downtime_cost_inr=gbp_to_inr_whole(metadata[2] if metadata else 1800),
        components=_machine_components(machine),
        history=history,
        fault_events=fault_events,
    )


@app.get("/machines/{machine_id}/telemetry", response_model=TelemetryResponse, tags=["machines"], dependencies=[Depends(require_authenticated)])
async def telemetry(
    machine_id: str,
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    interval: int = Query(60, gt=0, le=3600),
    channels: str | None = None,
) -> TelemetryResponse:
    _find_machine(machine_id)
    selected = [channel for channel in (channels.split(",") if channels else CHANNELS[:3]) if channel in CHANNELS]
    replay_t0 = datetime.fromtimestamp(1740528000, tz=UTC)
    start = from_ts or replay_t0 - timedelta(hours=6)
    end = to_ts or replay_t0 + timedelta(hours=6)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    source_points = read_parquet_points(machine_id, start_ms, end_ms)
    source_points.extend(read_ingest_points(machine_id, start_ms, end_ms))
    source_points.sort(key=lambda point: point.ts_ms)
    points: list[TelemetryPoint] = []
    previous_ts: int | None = None
    for point in source_points[:: max(1, interval // 30)]:
        quality_flags: list[str] = []
        if previous_ts is None or point.ts_ms - previous_ts > interval * 2 * 1000:
            quality_flags.append("missing")
        if any(channel not in point.channels for channel in selected):
            quality_flags.append("interpolated")
        if point.channels.get("vibration_rms_mm_s", 0) > 30:
            quality_flags.append("despiked")
        points.append(TelemetryPoint(ts=point.ts, channels={channel: round(point.channels[channel], 3) for channel in selected if channel in point.channels}, quality_flags=list(dict.fromkeys(quality_flags))))
        previous_ts = point.ts_ms
    if not points and machine_id == "CONV-14":
        duration = max(1, int((end - start).total_seconds()))
        for index in range(6):
            timestamp = start + timedelta(seconds=duration * index / 5)
            progress = index / 5
            quality = ["interpolated"] if progress < 0.2 else ["missing"] if progress < 0.6 else ["despiked"]
            points.append(TelemetryPoint(ts=timestamp, channels={}, quality_flags=quality))
    return TelemetryResponse(machine_id=machine_id, from_ts=start, to_ts=end, interval_seconds=interval, channels=selected, series=points)


@app.get("/machines/{machine_id}/prediction", response_model=PredictionResponse, tags=["machines"], dependencies=[Depends(require_authenticated)])
async def prediction(machine_id: str) -> PredictionResponse:
    machine = _find_machine(machine_id)
    fallback, context = _diagnosis_context(machine)
    sensor_result = await asyncio.to_thread(
        SensorAgent(
            PlantTSDBClient(database_url=settings.db_url),
            _prediction_engine(),
            database_url=settings.db_url,
        ).run,
        RunContext(trace_id=f"trace-prediction-{uuid4().hex[:12]}", payload=context.payload),
    )
    if not sensor_result.ok or sensor_result.output.get("refused") or not sensor_result.output.get("mode_probs"):
        raise HTTPException(status_code=503, detail={"code": "prediction_unavailable", "message": "A prediction could not be produced from the latest complete telemetry window"})
    result = _prediction_from_sensor(machine, fallback, sensor_result)
    append_event(
        actor="prediction-service",
        action="prediction_issued",
        subject=result.prediction_id,
        payload={
            "machine_id": result.machine_id,
            "p_failure_24h": result.p_failure_24h,
            "risk": result.risk,
            "band": result.band,
            "model_version": result.model_version,
        },
        database_url=settings.db_url,
    )
    return result


@app.post("/machines/{machine_id}/diagnose", response_model=DiagnoseResponse, tags=["diagnosis"], dependencies=[Depends(require_role("technician", "planner", "admin"))])
async def diagnose(machine_id: str, request: DiagnoseRequest | None = None) -> DiagnoseResponse:
    machine = _find_machine(machine_id)
    offline_mode = _llm_offline_state().effective_offline
    prediction_boundary, _ = _diagnosis_context(machine)
    cache_key = (prediction_boundary.prediction_id, offline_mode, str(settings.llm.models.coordinator.name))
    if not (request and request.force_refresh):
        cached = _cached_diagnosis(cache_key)
        if cached is not None:
            return cached
    trace_id = f"trace-{uuid4().hex[:12]}"
    diagnosis_result, run_result = await _execute_diagnosis(
        machine,
        trace_id=trace_id,
        offline_mode=offline_mode,
    )
    diagnosis_result = diagnosis_result.model_copy(update={"trace_id": trace_id})
    _persist_diagnosis(trace_id, diagnosis_result)
    _ensure_routed_approval(diagnosis_result, run_result.route)
    response = DiagnoseResponse(trace_id=trace_id, diagnosis=diagnosis_result)
    if diagnosis_result.generated or offline_mode:
        _cache_diagnosis(cache_key, response)
    return response


@app.get("/diagnoses/{diagnosis_id}", response_model=DiagnosisResponse, tags=["diagnosis"], dependencies=[Depends(require_authenticated)])
async def diagnosis(diagnosis_id: str) -> DiagnosisResponse:
    with session_scope(settings.db_url) as session:
        row = session.get(DiagnosisRow, diagnosis_id)
        payload = dict(row.response_json) if row is not None and isinstance(row.response_json, Mapping) else None
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "diagnosis_not_found", "message": f"Unknown diagnosis: {diagnosis_id}"})
    return DiagnosisResponse.model_validate(payload)


@app.get("/runs/{trace_id}", response_model=RunResponse, tags=["diagnosis"], dependencies=[Depends(require_authenticated)])
async def run(trace_id: str) -> RunResponse:
    persisted = load_agent_runs(trace_id)
    if not persisted:
        raise HTTPException(status_code=404, detail={"code": "run_not_found", "message": f"Unknown diagnosis run: {trace_id}"})

    runs: list[AgentRunResponse] = []
    for item in persisted:
        result_meta = item.output.get("_agent_result", {})
        ok = bool(result_meta.get("ok", not item.errors))
        state = "fallback" if item.fallback_used else "failed" if not ok or item.errors else "completed"
        verdict = "not_applicable" if state == "fallback" else "rejected" if state == "failed" else "accepted"
        runs.append(
            AgentRunResponse(
                agent=item.agent,
                state=state,
                latency_ms=float(item.latency_ms),
                prompt_tokens=item.prompt_tokens,
                completion_tokens=item.completion_tokens,
                model=item.model_name,
                verdict=verdict,
                error=item.errors[0] if item.errors else None,
                prompt=item.output.get("prompt"),
                raw_response=item.output.get("raw_response", item.output),
                guardrail_verdict=item.output.get("guardrail_verdict", verdict),
            )
        )
    has_failure = any(not bool(item.output.get("_agent_result", {}).get("ok", not item.errors)) for item in persisted)
    total_latency = sum(item.latency_ms for item in persisted)
    started_at = datetime.fromtimestamp(min(item.created_at for item in persisted) / 1000, tz=UTC)
    return RunResponse(
        trace_id=trace_id,
        status="failed" if has_failure else "completed",
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=total_latency),
        runs=runs,
    )


def _alert_response(row: sqlite3.Row) -> Alert:
    machine_id = row["machine_id"]
    kind = row["kind"]
    severity = row["severity"] if row["severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL", "NODATA"} else "MEDIUM"
    title = f"{machine_id} data quality degraded" if kind == "data" else f"{machine_id} predicted fault risk elevated"
    message = "Telemetry quality is insufficient for a reliable mechanical prediction." if kind == "data" else "The signal crossed the configured prediction threshold; review the next planned window."
    return Alert(
        id=row["id"], machine_id=machine_id, kind=kind, severity=severity, state=row["state"], title=title,
        message=message, created_at=datetime.fromtimestamp(row["created_ts"] / 1000, tz=UTC), updated_at=datetime.fromtimestamp(row["updated_ts"] / 1000, tz=UTC),
    )


def _alert_rows() -> list[sqlite3.Row]:
    try:
        with _db_connection() as connection:
            return connection.execute("SELECT id, machine_id, kind, severity, state, created_ts, updated_ts FROM alerts ORDER BY updated_ts DESC").fetchall()
    except sqlite3.Error:
        return []


@app.get("/alerts", response_model=list[Alert], tags=["alerts"], dependencies=[Depends(require_authenticated)])
async def alerts(state: AlertState | None = None, band: Band | None = None) -> list[Alert]:
    values = [_alert_response(row) for row in _alert_rows()]
    return [alert for alert in values if (state is None or alert.state == state) and (band is None or alert.severity == band)]


@app.patch("/alerts/{alert_id}", response_model=Alert, tags=["alerts"], dependencies=[Depends(require_role("technician", "planner", "admin"))])
async def update_alert(alert_id: str, payload: AlertPatch) -> Alert:
    now_ms = int(_utcnow().timestamp() * 1000)
    with _db_connection() as connection:
        row = connection.execute("SELECT id, machine_id, kind, severity, state, created_ts, updated_ts FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "alert_not_found", "message": f"Unknown alert: {alert_id}"})
        connection.execute("UPDATE alerts SET state = ?, updated_ts = ? WHERE id = ?", (payload.state, now_ms, alert_id))
        row = connection.execute("SELECT id, machine_id, kind, severity, state, created_ts, updated_ts FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return _alert_response(row)


_APPROVAL_ID_RE = re.compile(r"^approval-([1-9][0-9]*)$")


def _numeric_approval_id(approval_id: str) -> int:
    match = _APPROVAL_ID_RE.fullmatch(approval_id)
    if match is None:
        raise HTTPException(status_code=404, detail={"code": "approval_not_found", "message": f"Unknown approval: {approval_id}"})
    return int(match.group(1))


_APPROVAL_SELECT = """
    SELECT a.id, a.prediction_id, p.machine_id, a.autonomy_level, a.required_role,
           a.state, a.actor, a.reason, a.decided_at, a.action_payload,
           a.payload_hash, a.erp_sync_status, a.erp_workorder_id
    FROM approvals AS a
    JOIN predictions AS p ON p.id = a.prediction_id
"""


def _approval_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _approval_response(row: sqlite3.Row) -> Approval:
    autonomy = str(row["autonomy_level"])
    return Approval(
        id=f"approval-{row['id']}",
        prediction_id=str(row["prediction_id"]),
        machine_id=str(row["machine_id"]),
        autonomy_level=autonomy,
        required_role=str(row["required_role"]),
        state=str(row["state"]),
        decision_action="acknowledge" if autonomy == "A1" else "approve",
        action_payload=_approval_payload(row["action_payload"]),
        payload_hash=str(row["payload_hash"]) if row["payload_hash"] else None,
        actor=str(row["actor"]) if row["actor"] else None,
        reason=str(row["reason"]) if row["reason"] else None,
        decided_at=datetime.fromtimestamp(int(row["decided_at"]) / 1000, tz=UTC) if row["decided_at"] else None,
        erp_sync_status=str(row["erp_sync_status"] or "not_requested"),
        erp_workorder_id=str(row["erp_workorder_id"]) if row["erp_workorder_id"] else None,
    )


def _approval_database_row(approval_id: str) -> sqlite3.Row:
    numeric_id = _numeric_approval_id(approval_id)
    with _db_connection() as connection:
        row = connection.execute(f"{_APPROVAL_SELECT} WHERE a.id = ?", (numeric_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "approval_not_found", "message": f"Unknown approval: {approval_id}"})
    return row


def _machine_for_prediction(prediction_id: str) -> MachineSummary:
    with _db_connection() as connection:
        row = connection.execute("SELECT machine_id FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "prediction_not_found", "message": f"Unknown prediction: {prediction_id}"})
    return _find_machine(str(row["machine_id"]))


class _ERPReadFailure(RuntimeError):
    pass


def _erp_read(path: str, *, params: dict[str, Any] | None = None) -> Any:
    """Read from ERP without exposing its service credential to the browser."""

    try:
        response = httpx.get(
            f"{settings.erp.base_url.rstrip('/')}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {settings.erp.api_key or 'dev-erp-key'}", "X-ERP-Scopes": "workorder:read"},
            params=params,
            timeout=settings.erp.timeout_s,
        )
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        raise _ERPReadFailure(_exception_text(exc)) from exc
    if response.status_code >= 400:
        raise _ERPReadFailure(f"ERP returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise _ERPReadFailure("ERP returned invalid JSON") from exc


def _erp_datetime(value: Any, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value not in {None, ""}:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = fallback or datetime.now(UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _erp_workorder(row: dict[str, Any]) -> ERPWorkOrder:
    created_at = _erp_datetime(row.get("created_at"))
    status_value = str(row.get("status") or "completed")
    if status_value not in {"open", "in_progress", "completed", "cancelled"}:
        status_value = "completed"
    priority = str(row.get("priority") or "medium")
    if priority not in {"low", "medium", "high", "critical"}:
        priority = "medium"
    order_type = str(row.get("order_type") or "corrective")
    if order_type not in {"preventive", "corrective", "inspection"}:
        order_type = "corrective"
    raw_parts = row.get("parts") if "parts" in row else row.get("parts_used")
    if isinstance(raw_parts, str):
        try:
            decoded = json.loads(raw_parts)
            raw_parts = decoded if isinstance(decoded, list) else raw_parts.split(",")
        except ValueError:
            raw_parts = raw_parts.split(",")
    parts = [str(part.get("name") or part.get("part") or "unspecified") if isinstance(part, dict) else str(part) for part in (raw_parts or [])]
    completed_at = row.get("completed_at")
    if completed_at is None and row.get("completed_ts") is not None:
        completed_at = datetime.fromtimestamp(int(row["completed_ts"]) / 1000, tz=UTC)
    updated_at = row.get("updated_at") or completed_at or created_at
    return ERPWorkOrder(
        workorder_id=str(row.get("workorder_id") or ""),
        asset_id=str(row.get("asset_id") or row.get("machine_id") or ""),
        fault_code=str(row.get("fault_code") or "UNKNOWN"),
        description=str(row.get("description") or row.get("technician_notes") or "No description recorded"),
        priority=priority,  # type: ignore[arg-type]
        status=status_value,  # type: ignore[arg-type]
        created_at=created_at,
        updated_at=_erp_datetime(updated_at, fallback=created_at),
        scheduled_start=_erp_datetime(row["scheduled_start"]) if row.get("scheduled_start") else None,
        completed_at=_erp_datetime(completed_at) if completed_at else None,
        technician_notes=str(row.get("technician_notes") or ""),
        parts=parts,
        labour_hours=float(row.get("labour_hours") or 0),
        downtime_minutes=int(row.get("downtime_minutes") or 0),
        cost_inr=gbp_to_inr(float(row.get("cost_gbp") or 0)),
        lead_time_days=int(row.get("lead_time_days") or 0),
        order_type=order_type,  # type: ignore[arg-type]
    )


def _cached_erp_workorders() -> list[dict[str, Any]]:
    try:
        with _db_connection() as connection:
            rows = connection.execute(
                """SELECT workorder_id, machine_id, fault_code, technician_notes, parts_used,
                          labour_hours, downtime_minutes, cost_gbp, lead_time_days, order_type,
                          status, created_ts, completed_ts
                   FROM work_orders ORDER BY created_ts DESC"""
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            **dict(row),
            "asset_id": row["machine_id"],
            "description": row["technician_notes"],
            "created_at": datetime.fromtimestamp(row["created_ts"] / 1000, tz=UTC),
        }
        for row in rows
    ]


def _erp_sync_details(prediction_id: str) -> tuple[str, str | None]:
    for row in ERP_ACTION_AGENT.pending_syncs(limit=500):
        if row["prediction_id"] == prediction_id:
            return ("failed" if row["status"] == "failed" else "pending_sync"), None
    try:
        with _db_connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM audit_ledger WHERE action = 'erp_write' AND subject = ? ORDER BY seq DESC LIMIT 1",
                (prediction_id,),
            ).fetchone()
    except sqlite3.Error:
        row = None
    if row is None:
        return "not_requested", None
    payload_value = row["payload_json"]
    if isinstance(payload_value, str):
        try:
            payload_value = json.loads(payload_value)
        except ValueError:
            payload_value = {}
    workorder_id = str(payload_value.get("workorder_id")) if isinstance(payload_value, dict) and payload_value.get("workorder_id") else None
    return "synced", workorder_id


@app.get("/erp/status", response_model=ERPStatus, tags=["erp"], dependencies=[Depends(require_authenticated)])
def erp_status() -> ERPStatus:
    queue_rows = ERP_ACTION_AGENT.pending_syncs(limit=100)
    queue = [
        ERPSyncItem(
            prediction_id=str(row["prediction_id"]),
            status="failed" if row["status"] == "failed" else "pending",
            attempts=int(row["attempts"]),
            last_error=str(row["last_error"]) if row["last_error"] else None,
            updated_at=datetime.fromtimestamp(int(row["updated_ts"]) / 1000, tz=UTC),
        )
        for row in queue_rows
    ]
    try:
        health_payload = _erp_read("/erp/v1/health")
        rows = _erp_read("/erp/v1/workorders", params={"limit": 500})
        if not isinstance(health_payload, dict) or health_payload.get("status") != "healthy" or not isinstance(rows, list):
            raise _ERPReadFailure("ERP health response is degraded")
        source = "live"
        detail = "ERP is reachable; reads and approved writes are operational."
        historical_count = len(rows)
    except _ERPReadFailure as exc:
        source = "cache"
        detail = f"ERP is unavailable; historical reads use the local mirror. Approved writes remain queued. {exc}"
        historical_count = len(_cached_erp_workorders())
    pending_count = sum(item.status == "pending" for item in queue)
    failed_count = sum(item.status == "failed" for item in queue)
    return ERPStatus(
        status="healthy" if source == "live" and pending_count == 0 and failed_count == 0 else "degraded",
        source=source,
        historical_workorders=historical_count,
        pending_sync=pending_count,
        failed_sync=failed_count,
        queue=queue,
        detail=detail,
        generated_at=datetime.now(UTC),
    )


@app.get("/erp/assets/{asset_id}", response_model=ERPAsset, tags=["erp"], dependencies=[Depends(require_authenticated)])
def erp_asset(asset_id: str) -> ERPAsset:
    try:
        row = _erp_read(f"/erp/v1/assets/{asset_id}")
        if not isinstance(row, dict):
            raise _ERPReadFailure("ERP asset response is invalid")
        return ERPAsset(**row, source="live")
    except _ERPReadFailure:
        machine = _find_machine(asset_id)
        return ERPAsset(
            asset_id=machine.id,
            name=machine.name,
            asset_type=machine.type,
            model=machine.model,
            criticality=machine.criticality,
            cost_centre=f"CC-{machine.line}",
            line=machine.line,
            status="offline" if machine.status == "offline" else "degraded" if machine.status == "degraded" else "operational",
            source="cache",
        )


@app.get("/erp/workorders", response_model=ERPWorkOrderList, tags=["erp"], dependencies=[Depends(require_authenticated)])
def erp_workorders(
    asset_id: str | None = None,
    since: datetime | None = None,
    workorder_status: str | None = Query(default=None, alias="status", pattern=r"^(open|in_progress|completed|cancelled)$"),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> ERPWorkOrderList:
    params: dict[str, Any] = {"limit": 500}
    if asset_id:
        params["asset_id"] = asset_id
    if since:
        params["since"] = since.isoformat()
    try:
        payload = _erp_read("/erp/v1/workorders", params=params)
        if not isinstance(payload, list):
            raise _ERPReadFailure("ERP work-order response is invalid")
        rows = [dict(row) for row in payload if isinstance(row, dict)]
        ERP_ACTION_AGENT.mirror_work_orders(rows)
        source, detail = "live", "Live ERP history"
    except _ERPReadFailure as exc:
        rows = _cached_erp_workorders()
        source, detail = "cache", f"Local history mirror; ERP unavailable: {exc}"
        if asset_id:
            rows = [row for row in rows if row.get("machine_id") == asset_id]
        if since:
            rows = [row for row in rows if _erp_datetime(row.get("created_at")) >= since]
    if workorder_status:
        rows = [row for row in rows if row.get("status") == workorder_status]
    if q:
        needle = q.casefold()
        rows = [row for row in rows if needle in " ".join(str(row.get(key) or "") for key in ("workorder_id", "asset_id", "machine_id", "fault_code", "description", "technician_notes")).casefold()]
    items = [_erp_workorder(row) for row in rows[:limit]]
    return ERPWorkOrderList(items=items, total=len(rows), source=source, degraded=source == "cache", detail=detail)


@app.get("/erp/parts", response_model=ERPPartList, tags=["erp"], dependencies=[Depends(require_authenticated)])
def erp_parts(fault_code: str | None = None) -> ERPPartList:
    try:
        payload = _erp_read("/erp/v1/parts", params={"fault_code": fault_code} if fault_code else None)
        if not isinstance(payload, list):
            raise _ERPReadFailure("ERP parts response is invalid")
        rows = [dict(row) for row in payload if isinstance(row, dict)]
        source, detail = "live", "Live ERP inventory"
    except _ERPReadFailure as exc:
        from erp.db import PARTS

        rows = [dict(row) for row in PARTS if fault_code is None or row["fault_code"] == fault_code]
        source, detail = "cache", f"Fixture inventory; ERP unavailable: {exc}"
    items = [
        ERPPart(
            part_id=str(row["part_id"]), name=str(row["name"]), fault_code=str(row["fault_code"]),
            in_stock=bool(row["in_stock"]), stock_quantity=int(row["stock_quantity"]),
            lead_time_days=int(row["lead_time_days"]), unit_cost_inr=gbp_to_inr(float(row["unit_cost_gbp"])),
        )
        for row in rows
    ]
    return ERPPartList(items=items, source=source, degraded=source == "cache", detail=detail)


@app.post("/erp/workorders", response_model=ERPActionResponse, tags=["erp"])
def create_approved_erp_workorder(
    payload: ERPApprovalExecute,
    current_user: Principal = Depends(require_role("planner", "admin")),
) -> ERPActionResponse:
    row = _approval_database_row(payload.approval_id)
    if row["state"] != "approved" or row["autonomy_level"] != "A0":
        raise HTTPException(status_code=409, detail={"code": "approved_workflow_required", "message": "A persisted approved A0 decision is required"})
    result = _execute_approved_workorder(row, current_user)
    pending = result.get("status") == "pending_sync"
    workorder_id = str(result["workorder_id"]) if result.get("workorder_id") else None
    return ERPActionResponse(
        status="pending_sync" if pending else "synced",
        prediction_id=str(row["prediction_id"]),
        workorder_id=workorder_id,
        message="ERP unavailable; the approved work order is queued for automatic sync." if pending else f"Work order {workorder_id} is available in ERP.",
    )


@app.get("/approvals", response_model=list[Approval], tags=["approvals"], dependencies=[Depends(require_authenticated)])
async def approvals(state: ApprovalState | None = None) -> list[Approval]:
    clause = " WHERE a.state = ?" if state is not None else ""
    parameters: tuple[Any, ...] = (state,) if state is not None else ()
    with _db_connection() as connection:
        rows = connection.execute(f"{_APPROVAL_SELECT}{clause} ORDER BY a.id DESC", parameters).fetchall()
    return [_approval_response(row) for row in rows]


def _role_can_decide(user: Principal, required_role: str) -> bool:
    return user.role == "admin" or user.role == required_role


def _require_pending_decision(row: sqlite3.Row, current_user: Principal, *, autonomy: str) -> None:
    if row["state"] != "pending":
        raise HTTPException(status_code=409, detail={"code": "approval_not_pending", "message": "This approval has already been decided"})
    if row["autonomy_level"] != autonomy:
        raise HTTPException(status_code=409, detail={"code": "approval_action_mismatch", "message": f"This action is not valid for {row['autonomy_level']}"})
    if not _role_can_decide(current_user, str(row["required_role"])):
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": f"{row['required_role']} role required"})


def _transition_approval(approval_id: str, state_value: str, actor: str, reason: str | None) -> sqlite3.Row:
    numeric_id = _numeric_approval_id(approval_id)
    decision_time = int(_utcnow().timestamp() * 1000)
    with _db_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """UPDATE approvals SET state = ?, actor = ?, reason = ?, decided_at = ?
               WHERE id = ? AND state = 'pending'""",
            (state_value, actor, reason, decision_time, numeric_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail={"code": "approval_not_pending", "message": "This approval has already been decided"})
        row = connection.execute(f"{_APPROVAL_SELECT} WHERE a.id = ?", (numeric_id,)).fetchone()
    if row is None:  # pragma: no cover - protected by the successful update
        raise HTTPException(status_code=404, detail={"code": "approval_not_found", "message": f"Unknown approval: {approval_id}"})
    return row


def _set_erp_sync(approval_id: int, status_value: str, workorder_id: str | None = None) -> None:
    with _db_connection() as connection:
        connection.execute(
            "UPDATE approvals SET erp_sync_status = ?, erp_workorder_id = COALESCE(?, erp_workorder_id) WHERE id = ?",
            (status_value, workorder_id, approval_id),
        )


def _execute_approved_workorder(row: sqlite3.Row, current_user: Principal) -> dict[str, Any]:
    action_payload = _approval_payload(row["action_payload"])
    payload_hash = str(row["payload_hash"] or "")
    if action_payload is None or not payload_hash or _canonical_payload_hash(action_payload) != payload_hash:
        _set_erp_sync(int(row["id"]), "failed")
        raise HTTPException(status_code=409, detail={"code": "approval_payload_invalid", "message": "The immutable approved payload is missing or invalid"})
    values = {**action_payload, "approval_id": f"approval-{row['id']}", "payload_hash": payload_hash}
    try:
        result = ERP_ACTION_AGENT.create_work_order(values, actor=current_user.username, role=current_user.role)
    except PermissionError as exc:
        _set_erp_sync(int(row["id"]), "failed")
        raise HTTPException(status_code=409, detail={"code": "approved_workflow_required", "message": str(exc)}) from exc
    except ERPClientError as exc:
        _set_erp_sync(int(row["id"]), "failed")
        raise HTTPException(status_code=502, detail={"code": "erp_rejected_write", "message": str(exc)}) from exc
    pending = result.get("status") == "pending_sync"
    workorder_id = str(result["workorder_id"]) if result.get("workorder_id") else None
    _set_erp_sync(int(row["id"]), "pending_sync" if pending else "synced", workorder_id)
    return result


@app.post("/approvals/{approval_id}/approve", response_model=Approval, tags=["approvals"])
async def approve(approval_id: str, payload: ApprovalDecision | None = None, current_user: Principal = Depends(require_authenticated)) -> Approval:
    if current_user.role not in {"planner", "admin"}:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "planner role required"})
    row = _approval_database_row(approval_id)
    _require_pending_decision(row, current_user, autonomy="A0")
    reason = payload.reason if payload else None
    decided = _transition_approval(approval_id, "approved", current_user.username, reason)
    append_event(
        actor=current_user.username,
        action="approval",
        subject=approval_id,
        payload={"prediction_id": decided["prediction_id"], "reason": reason, "state": "approved", "payload_hash": decided["payload_hash"], "erp_write_requested": True},
        database_url=settings.db_url,
    )
    _execute_approved_workorder(decided, current_user)
    return _approval_response(_approval_database_row(approval_id))


@app.post("/approvals/{approval_id}/acknowledge", response_model=Approval, tags=["approvals"])
async def acknowledge(approval_id: str, payload: ApprovalDecision | None = None, current_user: Principal = Depends(require_authenticated)) -> Approval:
    if current_user.role not in {"technician", "admin"}:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "technician role required"})
    row = _approval_database_row(approval_id)
    _require_pending_decision(row, current_user, autonomy="A1")
    reason = payload.reason if payload else None
    decided = _transition_approval(approval_id, "acknowledged", current_user.username, reason)
    append_event(
        actor=current_user.username,
        action="acknowledgement",
        subject=approval_id,
        payload={"prediction_id": decided["prediction_id"], "reason": reason, "state": "acknowledged", "erp_write_requested": False},
        database_url=settings.db_url,
    )
    return _approval_response(decided)


@app.post("/approvals/{approval_id}/reject", response_model=Approval, tags=["approvals"])
async def reject(approval_id: str, payload: RejectRequest, current_user: Principal = Depends(require_authenticated)) -> Approval:
    if current_user.role == "operator":
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "approval role required"})
    row = _approval_database_row(approval_id)
    _require_pending_decision(row, current_user, autonomy=str(row["autonomy_level"]))
    decided = _transition_approval(approval_id, "rejected", current_user.username, payload.reason)
    approval = _approval_response(decided)
    append_event(
        actor=current_user.username,
        action="rejection",
        subject=approval.id,
        payload={"prediction_id": approval.prediction_id, "reason": payload.reason, "state": approval.state},
        database_url=settings.db_url,
    )
    return approval


@app.get("/schedule", response_model=list[ScheduleWindow], tags=["schedule"], dependencies=[Depends(require_authenticated)])
async def schedule(from_ts: datetime | None = Query(None, alias="from"), to_ts: datetime | None = Query(None, alias="to")) -> list[ScheduleWindow]:
    start = from_ts or datetime.fromtimestamp(1740528000, tz=UTC)
    end = to_ts or start + timedelta(days=14)
    values: list[ScheduleWindow] = []
    for machine in MACHINES:
        events = [event for event in _fault_events(machine.id) if start <= event.onset_ts <= end]
        if events:
            event = events[0]
            values.append(ScheduleWindow(id=f"window-{event.event_id}", machine_id=machine.id, machine_name=machine.name, start=event.onset_ts, end=event.failure_ts or event.onset_ts + timedelta(days=1), kind="predicted", fault_mode=event.mode, confidence=0.87, status="recommended"))
    return values


@app.post("/ingest/batch", response_model=IngestBatchResponse, tags=["ingest"], dependencies=[Depends(require_role("admin"))])
async def ingest_batch(payload: IngestBatchRequest) -> IngestBatchResponse:
    result = process_batch(payload.points, known_machines=[machine.id for machine in MACHINES], alert_generator=ALERT_GENERATOR, on_event=BROKER.publish)
    return IngestBatchResponse(accepted=result.accepted, rejected=result.rejected, dlq_ids=list(result.dlq_ids))


@app.get("/ingest/dlq", response_model=list[DlqItem], tags=["ingest"], dependencies=[Depends(require_authenticated)])
async def ingest_dlq() -> list[DlqItem]:
    return [DlqItem.model_validate(item) for item in read_dlq()]


def _ledger_default_tau() -> float:
    manifest_path = runtime_paths.model_root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        value = float(manifest.get("threshold_tau", settings.predict.threshold_tau_default))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        value = settings.predict.threshold_tau_default
    return value if 0 <= value <= 1 else settings.predict.threshold_tau_default


def _ledger_simulation(tau: float) -> DowntimeSimulationResult:
    try:
        with _db_connection() as connection:
            events = connection.execute(
                "SELECT id AS event_id, machine_id, onset_ts, failure_ts, actual_downtime_min FROM fault_events ORDER BY id"
            ).fetchall()
            machine_costs = connection.execute(
                "SELECT id AS machine_id, hourly_downtime_cost_gbp FROM machines ORDER BY id"
            ).fetchall()
            history = connection.execute(
                "SELECT machine_id, order_type, cost_gbp FROM work_orders ORDER BY workorder_id"
            ).fetchall()
            signals = connection.execute(
                """SELECT id AS signal_id, machine_id, ts, p_failure AS probability
                   FROM predictions WHERE source = 'historical_backfill'
                   ORDER BY machine_id, ts, id"""
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("ledger input unavailable: %s", exc)
        raise HTTPException(status_code=503, detail={"code": "ledger_unavailable", "message": "Downtime ledger data is unavailable"}) from exc

    if not signals:
        raise HTTPException(
            status_code=503,
            detail={"code": "ledger_predictions_unavailable", "message": "Historical time-indexed predictions have not been backfilled"},
        )
    return simulate_downtime(
        events,
        machine_costs,
        signals,
        tau=tau,
        erp_history=history,
        probability_snapshots=True,
    )


def _ledger_missed_failures(result: DowntimeSimulationResult) -> list[LedgerMissedFailure]:
    missed_ids = set(result.predictive.missed_failure_ids)
    if not missed_ids:
        return []
    try:
        with _db_connection() as connection:
            rows = connection.execute(
                """
                SELECT f.id AS event_id, f.machine_id, f.mode, f.severity, f.onset_ts,
                       f.actual_downtime_min, m.hourly_downtime_cost_gbp
                FROM fault_events AS f
                JOIN machines AS m ON m.id = f.machine_id
                ORDER BY f.onset_ts, f.id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("ledger missed-failure data unavailable: %s", exc)
        raise HTTPException(status_code=503, detail={"code": "ledger_unavailable", "message": "Downtime ledger data is unavailable"}) from exc

    missed_by_machine = result.predictive.per_machine
    failures: list[LedgerMissedFailure] = []
    for row in rows:
        event_id = str(row["event_id"])
        if event_id not in missed_ids:
            continue
        machine_result = missed_by_machine.get(row["machine_id"])
        charged_cost = machine_result.missed_failure_costs_gbp.get(event_id) if machine_result else None
        if charged_cost is None:
            charged_cost = row["actual_downtime_min"] * row["hourly_downtime_cost_gbp"] / 60.0
        failures.append(
            LedgerMissedFailure(
                event_id=event_id,
                machine_id=row["machine_id"],
                fault_mode=row["mode"],
                severity=row["severity"],
                onset_ts=datetime.fromtimestamp(row["onset_ts"] / 1000, tz=UTC),
                cost_inr=gbp_to_inr_whole(max(0.0, charged_cost)),
            )
        )
    return failures


@app.get("/ledger", response_model=LedgerResponse, tags=["evaluation"], dependencies=[Depends(require_authenticated)])
async def ledger(tau: float | None = Query(None, ge=0, le=1)) -> LedgerResponse:
    if not settings.ui.features.ledger:
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "The downtime ledger feature is disabled"})
    result = _ledger_simulation(_ledger_default_tau() if tau is None else tau)
    return LedgerResponse(
        tau=result.tau,
        target_reduction_pct=20.0,
        reduction_pct=round(result.reduction_pct, 2),
        missed_failures=_ledger_missed_failures(result),
        policies=[
            LedgerPolicy(
                policy=name,
                downtime_minutes=int(round(policy.downtime_minutes)),
                avoided_cost_inr=gbp_to_inr_whole(max(0.0, result.reactive.cost_gbp - policy.cost_gbp)),
            )
            for name, policy in result.policies.items()
        ],
    )


@app.get("/eval/report", response_model=EvalReportResponse, tags=["evaluation"], dependencies=[Depends(require_authenticated)])
async def eval_report() -> EvalReportResponse:
    root = Path(__file__).resolve().parents[1]
    manifest_path = runtime_paths.model_root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    metrics_data = manifest.get("metrics", {})
    balanced = float(metrics_data.get("failure", {}).get("balanced_accuracy", 0.0))
    event_rate = float(metrics_data.get("per_event_detection", {}).get("rate", 0.0))
    summary: dict[str, Any] = {}
    report_path = root / "eval" / "report.json"
    if report_path.exists():
        try:
            value = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                summary = value
        except (OSError, json.JSONDecodeError):
            summary = {}
    injection_summary = summary.get("injection") if isinstance(summary, dict) else None
    if isinstance(injection_summary, dict) and "fixtures" in injection_summary:
        # Keep the persisted report shape compatible while exposing a
        # semantically named UI field that is clearly report data, not a UI
        # fixture dependency.
        summary = dict(summary)
        summary["injection"] = {**injection_summary, "notes": injection_summary["fixtures"]}

    metrics = [
        EvalMetric(name="balanced_accuracy", value=balanced, target=0.85, passed=balanced >= 0.85, sample_size=int(manifest.get("heldout_rows", 0))),
        EvalMetric(name="per_event_detection", value=event_rate, target=0.85, passed=event_rate >= 0.85, sample_size=int(metrics_data.get("per_event_detection", {}).get("total", 0))),
    ]

    def add_metric(name: str, value: Any, target: float | None, sample_size: Any = 0) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            metrics.append(EvalMetric(name=name, value=numeric, target=target, passed=numeric >= target if target is not None else True, sample_size=max(0, int(sample_size))))

    prediction = summary.get("prediction", {}) if isinstance(summary.get("prediction"), dict) else {}
    fault_mode = prediction.get("fault_mode", {}) if isinstance(prediction.get("fault_mode"), dict) else {}
    rul = prediction.get("rul", {}) if isinstance(prediction.get("rul"), dict) else {}
    groundedness = summary.get("groundedness", {}) if isinstance(summary.get("groundedness"), dict) else {}
    hallucination = summary.get("hallucination", {}) if isinstance(summary.get("hallucination"), dict) else {}
    reliability = summary.get("reliability", {}) if isinstance(summary.get("reliability"), dict) else {}
    add_metric("fault_mode_top1", fault_mode.get("top1_accuracy"), 0.70, fault_mode.get("sample_size", 0))
    add_metric("rul_interval_coverage", rul.get("p10_p90_coverage"), 0.75, rul.get("sample_size", 0))
    add_metric("citation_coverage", groundedness.get("citation_coverage"), 0.95, hallucination.get("citation_applicable", 0))
    add_metric("numeric_grounding_first_try", groundedness.get("numeric_grounding_first_try"), 0.95, hallucination.get("total", 0))
    add_metric("hallucination_set", (hallucination.get("passed", 0) / hallucination.get("total", 1)) if hallucination.get("total") else None, 1.0, hallucination.get("total", 0))
    add_metric("reliability", (reliability.get("completed", 0) / reliability.get("runs", 1)) if reliability.get("runs") else None, 1.0, reliability.get("runs", 0))
    generated_at = _utcnow()
    if summary.get("generated_at"):
        try:
            generated_at = datetime.fromisoformat(str(summary["generated_at"]).replace("Z", "+00:00"))
        except ValueError:
            pass
    notes = ["Metrics are read from the persisted model manifest."]
    if summary:
        notes.extend(["B-14 JSON evaluation summary is available to the Trust screen.", f"Evaluation measured mode: {summary.get('run_mode', 'unknown')}."])
    return EvalReportResponse(
        generated_at=generated_at,
        model_version=str(manifest.get("feature_version", "unknown")),
        prompt_version=str(settings.llm.prompt_version),
        run_mode=str(summary.get("run_mode", "unknown")),
        metrics=metrics,
        notes=notes,
        summary=summary,
    )


@app.get("/audit", response_model=list[AuditEntry], tags=["audit"], dependencies=[Depends(require_role("planner", "admin"))])
async def audit(since: datetime | None = Query(None)) -> list[AuditEntry]:
    try:
        with _db_connection() as connection:
            rows = connection.execute("SELECT seq, ts, actor, action, subject, payload_json, prev_hash, hash FROM audit_ledger ORDER BY seq").fetchall()
    except sqlite3.Error:
        rows = []
    values = [AuditEntry(seq=row["seq"], ts=datetime.fromtimestamp(row["ts"] / 1000, tz=UTC), actor=row["actor"], action=row["action"], subject=row["subject"], payload=json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else row["payload_json"], prev_hash=row["prev_hash"], hash=row["hash"]) for row in rows]
    return [entry for entry in values if since is None or entry.ts >= since]


@app.get("/audit/verify", response_model=AuditVerifyResponse, tags=["audit"], dependencies=[Depends(require_role("planner", "admin"))])
async def audit_verify() -> AuditVerifyResponse:
    result = verify_ledger(database_url=settings.db_url)
    return AuditVerifyResponse(
        intact=result.intact,
        entries=result.entries,
        checked_entries=result.checked_entries,
        first_invalid_seq=result.first_invalid_seq,
    )


@app.get("/security/quarantine", response_model=list[QuarantineEntry], tags=["security"], dependencies=[Depends(require_authenticated)])
async def security_quarantine(include_released: bool = Query(True)) -> list[QuarantineEntry]:
    """Expose injection quarantine state without exposing unredacted ERP text."""

    return [
        QuarantineEntry.model_validate(item)
        for item in list_quarantines(database_url=settings.db_url, include_released=include_released)
    ]


@app.post(
    "/security/quarantine/{quarantine_id}/release",
    response_model=QuarantineReleaseResponse,
    tags=["security"],
)
async def security_quarantine_release(
    quarantine_id: str,
    payload: QuarantineReleaseRequest,
    user: Principal = Depends(require_role("technician", "planner", "admin")),
) -> QuarantineReleaseResponse:
    """Record a human review decision; released text remains excluded from prompts."""

    try:
        item = release_quarantine(
            quarantine_id,
            actor=user.username,
            reason=payload.reason,
            database_url=settings.db_url,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail={"code": "quarantine_not_found", "message": "Quarantine record not found"}) from None
    if item["status"] != "released" or not item.get("released_at"):
        raise HTTPException(status_code=409, detail={"code": "quarantine_release_failed", "message": "Quarantine release was not recorded"})
    return QuarantineReleaseResponse(
        quarantine_id=item["quarantine_id"],
        status="released",
        reason=str(item.get("release_reason") or payload.reason),
        released_by=str(item.get("released_by") or user.username),
        released_at=datetime.fromisoformat(str(item["released_at"])),
    )


@app.post("/rehearsal/inject", response_model=RehearsalInjectResponse, tags=["rehearsal"])
async def rehearsal_inject(payload: RehearsalInjectRequest, user: Principal = Depends(require_role("technician", "planner", "admin"))) -> RehearsalInjectResponse:
    if not settings.ui.features.rehearsal:
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "The rehearsal feature is disabled"})
    _find_machine(payload.machine_id)
    onset = payload.onset if payload.onset.tzinfo is not None else payload.onset.replace(tzinfo=UTC)
    onset = onset.astimezone(UTC)
    raw_point = build_rehearsal_point(payload.machine_id, payload.mode, payload.severity, onset)
    result = process_batch(
        [raw_point],
        known_machines=[machine.id for machine in MACHINES],
        database_url=settings.db_url,
        alert_generator=ALERT_GENERATOR,
        on_event=BROKER.publish,
    )
    if result.rejected:
        raise HTTPException(status_code=422, detail={"code": "rehearsal_point_rejected", "message": "The generated rehearsal point did not pass the sensor contract"})

    event_id = f"sim-{uuid4().hex[:12]}"
    machine_alerts = tuple(alert for alert in result.alert_records if alert.kind == "machine")
    observed_mode = machine_alerts[-1].fault_mode if machine_alerts else None
    detected = bool(machine_alerts)
    mode_correct = payload.mode in MECHANICAL_MODES and observed_mode == payload.mode
    lead_time = (failure_horizon(onset, settings.predict.horizon_hours) - onset).total_seconds() / 3600 if detected and payload.mode in MECHANICAL_MODES else None
    record = RehearsalRecord(
        event_id=event_id,
        machine_id=payload.machine_id,
        mode=payload.mode,
        severity=payload.severity,
        onset=onset,
        detected=detected,
        observed_mode=observed_mode,
        mode_correct=mode_correct,
        lead_time_hours=round(max(0.0, lead_time), 3) if lead_time is not None else None,
        alert_ids=tuple(alert.id for alert in machine_alerts),
    )
    with _REHEARSAL_LOCK:
        REHEARSAL_EVENTS[event_id] = record
    _rehearsal_update_machine(payload.machine_id, risk_delta(payload.mode, payload.severity), onset)
    append_event(
        actor=user.username,
        action="rehearsal_inject",
        subject=event_id,
        payload={"machine_id": payload.machine_id, "mode": payload.mode, "severity": payload.severity, "onset": onset.isoformat(), "detected": detected},
        database_url=settings.db_url,
    )
    return RehearsalInjectResponse(event_id=event_id, machine_id=payload.machine_id, status="injected")


@app.post("/rehearsal/reset", response_model=RehearsalResetResponse, tags=["rehearsal"])
async def rehearsal_reset(user: Principal = Depends(require_role("technician", "planner", "admin"))) -> RehearsalResetResponse:
    if not settings.ui.features.rehearsal:
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "The rehearsal feature is disabled"})
    with _REHEARSAL_LOCK:
        REHEARSAL_EVENTS.clear()
    deleted_points = delete_simulated_points(settings.db_url)
    deleted_alerts = delete_simulated_alerts(settings.db_url)
    restored_machines = _rehearsal_restore_machines()
    append_event(
        actor=user.username,
        action="rehearsal_reset",
        subject="rehearsal",
        payload={"restored_machines": restored_machines, "deleted_simulated_points": deleted_points, "deleted_simulated_alerts": deleted_alerts},
        database_url=settings.db_url,
    )
    return RehearsalResetResponse(restored_machines=restored_machines, status="reset")


@app.get("/rehearsal/scoreboard", response_model=RehearsalScoreboardResponse, tags=["rehearsal"], dependencies=[Depends(require_authenticated)])
async def rehearsal_scoreboard() -> RehearsalScoreboardResponse:
    if not settings.ui.features.rehearsal:
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "The rehearsal feature is disabled"})
    with _REHEARSAL_LOCK:
        records = tuple(REHEARSAL_EVENTS.values())
    scenarios = [
        ScoreboardItem(
            scenario=record.mode,
            machine_id=record.machine_id,
            expected="decline_mechanical_fault" if record.mode == "sensor_drift" else record.mode,
            observed="declined_mechanical_fault" if record.mode == "sensor_drift" and not record.detected else record.observed_mode or "not_detected",
            passed=(not record.detected) if record.mode == "sensor_drift" else record.detected and record.mode_correct,
            detected=record.detected,
            lead_time_hours=record.lead_time_hours,
            mode_correct=record.mode_correct,
        )
        for record in records
    ]
    mechanical = tuple(record for record in records if record.mode in MECHANICAL_MODES)
    detected = tuple(record for record in mechanical if record.detected)
    detection_rate = len(detected) / len(mechanical) if mechanical else 0.0
    mode_accuracy = sum(record.mode_correct for record in mechanical) / len(mechanical) if mechanical else 0.0
    lead_times = [record.lead_time_hours for record in detected if record.lead_time_hours is not None]
    lead_time_hours = float(np.median(lead_times)) if lead_times else None
    return RehearsalScoreboardResponse(
        scenarios=scenarios,
        all_passed=all(item.passed for item in scenarios),
        detection_rate=round(detection_rate, 4),
        lead_time_hours=round(lead_time_hours, 3) if lead_time_hours is not None else None,
        mode_accuracy=round(mode_accuracy, 4),
    )


@app.post("/stream/speed", response_model=MessageResponse, tags=["stream"])
async def stream_speed(speed: float = Query(..., gt=0, le=600), _: Principal = Depends(require_role("admin"))) -> MessageResponse:
    global REPLAY_SPEED
    REPLAY_SPEED = speed
    if REPLAY_MANAGER is not None:
        REPLAY_MANAGER.clock.set_speed(speed)
    return MessageResponse(message=f"Replay speed set to {speed:g}x")


@app.post("/stream/pause", response_model=MessageResponse, tags=["stream"])
async def stream_pause(paused: bool = Query(...), _: Principal = Depends(require_role("admin"))) -> MessageResponse:
    if REPLAY_MANAGER is not None:
        REPLAY_MANAGER.clock.set_paused(paused)
    return MessageResponse(message="Replay paused" if paused else "Replay resumed")


@app.post("/stream/reset", response_model=MessageResponse, tags=["stream"])
async def stream_reset(_: Principal = Depends(require_role("admin"))) -> MessageResponse:
    """Reset the in-process replay clock to its configured plant ``t0``.

    This is intentionally an admin-only operational control.  Rehearsal
    tooling uses it between takes so a PUMP-07 alert is observed from a clean
    replay origin rather than from a partially consumed stream.  It does not
    delete sensor, alert, approval, or audit data.
    """

    global REPLAY_MANAGER, REPLAY_SPEED
    if REPLAY_MANAGER is not None:
        await REPLAY_MANAGER.stop()
    REPLAY_MANAGER = None
    REPLAY_SPEED = float(settings.stream.speedup)
    BROKER.clear_history()
    return MessageResponse(message="Replay clock reset to t0")


@app.get("/stream/events", tags=["stream"], dependencies=[Depends(require_authenticated)])
async def stream_events(request: Request, last_event_id: str | None = Query(None)) -> StreamingResponse:
    global REPLAY_MANAGER
    if REPLAY_MANAGER is None:
        clock = StreamClock(speedup=REPLAY_SPEED)
        measured_cue_seconds = _demo_alert_cue_seconds()

        def on_point(point: Any) -> None:
            # The planted PUMP-07 signature reaches the simple ingest alert
            # threshold before the tuned model crosses tau.  Keep the live
            # demo cue coupled to A-15's measured crossing without changing
            # external ingest semantics or the model-integrity measurement.
            if point.machine_id == "PUMP-07":
                cue_start_ms = clock.t0_ts_ms + int(measured_cue_seconds * clock.speedup * 1000)
                if point.ts_ms < cue_start_ms:
                    return
            alert = ALERT_GENERATOR.from_point(point)
            if alert:
                BROKER.publish("alert", {"alert_id": alert.id, "machine_id": alert.machine_id, "kind": alert.kind, "severity": alert.severity})

        REPLAY_MANAGER = ReplayManager([machine.id for machine in MACHINES], BROKER, clock=clock, on_point=on_point)
    await REPLAY_MANAGER.start()
    queue, backlog = BROKER.subscribe(request.headers.get("last-event-id") or last_event_id)

    async def events():
        try:
            pending = backlog
            while True:
                if pending:
                    record = pending.pop(0)
                else:
                    try:
                        record = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                event = json.dumps(record["data"], separators=(",", ":"), default=str)
                yield f"id: {record['id']}\nevent: {record['event']}\ndata: {event}\n\n"
                if await request.is_disconnected():
                    break
        finally:
            BROKER.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/meta", response_model=MetaResponse, tags=["system"], dependencies=[Depends(require_authenticated)])
async def meta() -> MetaResponse:
    return MetaResponse(bands={"critical": settings.fusion.bands["critical"], "high": settings.fusion.bands["high"], "medium": settings.fusion.bands["medium"]}, modes=MODES, weights=settings.fusion.weights, versions={"api": "0.1.0", "features": settings.features.version, "model": "fv1-demo-001"}, feature_flags={"digital_twin": settings.ui.features.digital_twin, "rehearsal": settings.ui.features.rehearsal, "ledger": settings.ui.features.ledger})
