"""Standalone mock ERP service used for real HTTP integration tests and demos."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from erp.db import Database, database_for_path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "erp" / "erp.db"
DEFAULT_API_KEY = "dev-erp-key"
ERP_SCOPES = frozenset({"workorder:read", "workorder:write"})
FAULT_MODES = frozenset({"none", "slow", "flaky", "down"})

load_dotenv(ROOT / ".env")


class ERPModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetResponse(ERPModel):
    asset_id: str
    name: str
    asset_type: str
    model: str
    criticality: int = Field(ge=1, le=5)
    cost_centre: str
    line: str
    status: Literal["operational", "degraded", "offline"]


class WorkOrderCreate(ERPModel):
    asset_id: str
    fault_code: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=500)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    scheduled_start: datetime | None = None
    technician_notes: str = Field(default="", max_length=2000)
    parts: list[str] = Field(default_factory=list, max_length=50)
    labour_hours: float = Field(default=0, ge=0, le=1000)
    downtime_minutes: int = Field(default=0, ge=0, le=100000)
    cost_gbp: float = Field(default=0, ge=0, le=10_000_000)


class WorkOrderPatch(ERPModel):
    status: Literal["open", "in_progress", "completed", "cancelled"] | None = None
    scheduled_start: datetime | None = None
    completed_at: datetime | None = None
    technician_notes: str | None = Field(default=None, max_length=2000)


class WorkOrderResponse(ERPModel):
    workorder_id: str
    asset_id: str
    fault_code: str
    description: str
    priority: Literal["low", "medium", "high", "critical"]
    status: Literal["open", "in_progress", "completed", "cancelled"]
    created_at: datetime
    updated_at: datetime
    scheduled_start: datetime | None
    completed_at: datetime | None
    technician_notes: str
    parts: list[str]
    labour_hours: float
    downtime_minutes: int
    cost_gbp: float
    lead_time_days: int = Field(ge=0)
    order_type: Literal["preventive", "corrective", "inspection"] = "corrective"


class PartResponse(ERPModel):
    part_id: str
    name: str
    fault_code: str
    in_stock: bool
    stock_quantity: int = Field(ge=0)
    lead_time_days: int = Field(ge=0)
    unit_cost_gbp: float = Field(ge=0)


class HealthResponse(ERPModel):
    service: Literal["mock-erp"]
    status: Literal["healthy", "degraded", "down"]
    database: Literal["up", "down"]
    fault_mode: Literal["none", "slow", "flaky", "down"]
    generated_at: datetime


app = FastAPI(
    title="SentinelPdM Mock ERP",
    version="0.1.0",
    description="A separate-process ERP fixture with bearer auth, idempotency, and chaos modes.",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _api_key() -> str:
    return os.getenv("ERP_API_KEY", DEFAULT_API_KEY).strip() or DEFAULT_API_KEY


def _database() -> Database:
    configured_path = os.getenv("ERP_DB_PATH", str(DEFAULT_DB_PATH))
    return database_for_path(Path(configured_path).resolve())


def _fault_mode() -> str:
    mode = os.getenv("ERP_FAULT_MODE", "none").strip().lower()
    if mode not in FAULT_MODES:
        raise HTTPException(status_code=500, detail={"code": "invalid_fault_mode", "message": f"Unsupported ERP_FAULT_MODE: {mode}"})
    return mode


class FaultInjector:
    """Applies deterministic 30% flaky failures without making tests probabilistic."""

    def __init__(self) -> None:
        self._counter = 0
        self._lock = threading.Lock()

    def flaky_failure(self) -> bool:
        with self._lock:
            request_number = self._counter
            self._counter += 1
        return request_number % 10 in {0, 1, 2}


fault_injector = FaultInjector()


async def fault_guard() -> None:
    mode = _fault_mode()
    if mode == "slow":
        await asyncio.sleep(3)
    elif mode == "flaky" and fault_injector.flaky_failure():
        raise HTTPException(status_code=503, detail={"code": "erp_flaky", "message": "Mock ERP transient failure"})
    elif mode == "down":
        raise HTTPException(status_code=503, detail={"code": "erp_down", "message": "Mock ERP is unavailable"})


def _unauthorized(message: str = "A valid ERP bearer token is required") -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "unauthorized", "message": message}, headers={"WWW-Authenticate": "Bearer"})


def _parse_credentials(authorization: str | None, requested_scopes: str | None) -> set[str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()
    token = authorization.removeprefix("Bearer ").strip()
    configured_key = _api_key()
    token_key = token
    token_scopes: set[str] = set(ERP_SCOPES)
    for separator in ("|", ":"):
        if token.startswith(f"{configured_key}{separator}"):
            token_key, encoded_scopes = token.split(separator, 1)
            token_scopes = {scope.strip() for scope in encoded_scopes.split(",") if scope.strip()}
            break
    if token_key != configured_key:
        raise _unauthorized("Invalid ERP bearer token")
    if requested_scopes is not None:
        token_scopes = {scope.strip() for scope in requested_scopes.split(",") if scope.strip()}
    return token_scopes


def require_scope(scope: str):
    async def dependency(
        authorization: Annotated[str | None, Header()] = None,
        erp_scopes: Annotated[str | None, Header(alias="X-ERP-Scopes")] = None,
    ) -> None:
        scopes = _parse_credentials(authorization, erp_scopes)
        if scope not in scopes:
            raise HTTPException(status_code=403, detail={"code": "insufficient_scope", "message": f"Scope required: {scope}"})

    return dependency


def _as_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_work_order(row: dict[str, object]) -> WorkOrderResponse:
    return WorkOrderResponse(
        workorder_id=str(row["workorder_id"]),
        asset_id=str(row["asset_id"]),
        fault_code=str(row["fault_code"]),
        description=str(row["description"]),
        priority=str(row["priority"]),
        status=str(row["status"]),
        created_at=_as_datetime(str(row["created_at"])) or _now(),
        updated_at=_as_datetime(str(row["updated_at"])) or _now(),
        scheduled_start=_as_datetime(str(row["scheduled_start"])) if row["scheduled_start"] else None,
        completed_at=_as_datetime(str(row["completed_at"])) if row["completed_at"] else None,
        technician_notes=str(row["technician_notes"] or ""),
        parts=[part for part in str(row["parts"] or "").split(",") if part],
        labour_hours=float(row["labour_hours"] or 0),
        downtime_minutes=int(row["downtime_minutes"] or 0),
        cost_gbp=float(row["cost_gbp"] or 0),
        lead_time_days=int(row.get("lead_time_days") or 0),
        order_type=str(row.get("order_type") or "corrective"),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> Response:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "erp_error", "message": str(exc.detail)}
    return Response(
        content=json.dumps({"error": detail}),
        status_code=exc.status_code,
        media_type="application/json",
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> Response:
    first_error = exc.errors()[0]
    location = ".".join(str(part) for part in first_error.get("loc", ()))
    return Response(
        content=json.dumps({"error": {"code": "validation_error", "message": f"Invalid field: {location}"}}),
        status_code=422,
        media_type="application/json",
    )


@app.get("/erp/v1/assets/{asset_id}", response_model=AssetResponse, dependencies=[Depends(require_scope("workorder:read")), Depends(fault_guard)], tags=["assets"])
async def get_asset(asset_id: str) -> AssetResponse:
    asset = _database().get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "asset_not_found", "message": f"Unknown asset: {asset_id}"})
    return AssetResponse(**asset)


@app.get("/erp/v1/workorders", response_model=list[WorkOrderResponse], dependencies=[Depends(require_scope("workorder:read")), Depends(fault_guard)], tags=["workorders"])
async def get_workorders(
    asset_id: str | None = None,
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[WorkOrderResponse]:
    rows = _database().list_workorders(asset_id=asset_id, since=since, limit=limit)
    return [_as_work_order(row) for row in rows]


@app.post("/erp/v1/workorders", response_model=WorkOrderResponse, status_code=201, dependencies=[Depends(require_scope("workorder:write")), Depends(fault_guard)], tags=["workorders"])
async def create_workorder(
    payload: WorkOrderCreate,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkOrderResponse:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail={"code": "idempotency_key_required", "message": "Idempotency-Key header is required"})
    if _database().get_asset(payload.asset_id) is None:
        raise HTTPException(status_code=404, detail={"code": "asset_not_found", "message": f"Unknown asset: {payload.asset_id}"})
    row, replayed = _database().create_workorder(payload.model_dump(mode="json"), idempotency_key.strip(), _now())
    if replayed:
        response.status_code = 200
    return _as_work_order(row)


@app.patch("/erp/v1/workorders/{workorder_id}", response_model=WorkOrderResponse, dependencies=[Depends(require_scope("workorder:write")), Depends(fault_guard)], tags=["workorders"])
async def update_workorder(workorder_id: str, payload: WorkOrderPatch) -> WorkOrderResponse:
    if not any(value is not None for value in payload.model_dump().values()):
        raise HTTPException(status_code=422, detail={"code": "empty_patch", "message": "At least one work-order field is required"})
    row = _database().update_workorder(workorder_id, payload.model_dump(exclude_unset=True), _now())
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "workorder_not_found", "message": f"Unknown work order: {workorder_id}"})
    return _as_work_order(row)


@app.get("/erp/v1/parts", response_model=list[PartResponse], dependencies=[Depends(require_scope("workorder:read")), Depends(fault_guard)], tags=["parts"])
async def get_parts(fault_code: str | None = None) -> list[PartResponse]:
    return [PartResponse(**part) for part in _database().list_parts(fault_code)]


@app.get("/erp/v1/health", response_model=HealthResponse, dependencies=[Depends(fault_guard)], tags=["system"])
async def health() -> HealthResponse:
    mode = _fault_mode()
    database_status = "up" if _database().healthcheck() else "down"
    return HealthResponse(service="mock-erp", status="healthy" if database_status == "up" and mode != "down" else "degraded", database=database_status, fault_mode=mode, generated_at=_now())
