"""Versioned API request and response contracts for the SentinelPdM service.

The API contract is intentionally independent from persistence and agent
implementations. Later workstreams extend the route handlers while these
models remain the boundary shared by the backend and UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Band = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "NODATA"]
MachineStatus = Literal["online", "degraded", "offline"]
AlertState = Literal["open", "acknowledged", "dismissed"]
ApprovalState = Literal["pending", "approved", "acknowledged", "rejected", "superseded"]
RehearsalMode = Literal["bearing_wear", "rotor_imbalance", "seal_leak", "motor_overheat", "lubrication_degradation", "sensor_drift"]


class ContractModel(BaseModel):
    """Base class enforcing the API's no-unknown-fields policy."""

    model_config = ConfigDict(extra="forbid")


class ErrorBody(ContractModel):
    code: str
    message: str
    trace_id: str


class ErrorResponse(ContractModel):
    error: ErrorBody


class LoginRequest(ContractModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(ContractModel):
    username: str
    role: Literal["operator", "technician", "planner", "admin"]
    expires_at: datetime
    can_switch_persona: bool = False


class CurrentUserResponse(ContractModel):
    username: str
    role: Literal["operator", "technician", "planner", "admin"]
    can_switch_persona: bool = False


class PersonaSwitchRequest(ContractModel):
    role: Literal["operator", "technician", "planner", "admin"]


class MessageResponse(ContractModel):
    message: str


class DependencyStatus(ContractModel):
    name: str
    status: Literal["up", "degraded", "down"]
    latency_ms: float = Field(ge=0)
    detail: str
    breaker_state: Literal["closed", "open", "half_open"] = "closed"
    tls_verified: bool = True
    reachable: bool | None = None
    model_name: str | None = None
    provider: str | None = None


class HealthResponse(ContractModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    dependencies: list[DependencyStatus]
    replay: Literal["running", "paused", "stopped"]
    model_version: str
    generated_at: datetime


class MetricsResponse(ContractModel):
    window: str
    requests: int = Field(ge=0)
    errors_by_code: dict[str, int]
    agent_latencies: dict[str, "AgentLatencyMetric"]
    guardrail_rejections_by_rule: dict[str, int]
    llm_tokens: "LLMTokenMetrics"
    dlq_depth: int = Field(ge=0)
    circuit_breaker_trips: dict[str, int]
    # These summary fields pre-date A-10 and remain available to existing
    # consumers while the seven-family observability contract is adopted.
    requests_total: int = Field(ge=0)
    diagnoses_total: int = Field(ge=0)
    alerts_open: int = Field(ge=0)
    prediction_latency_p95_ms: float = Field(ge=0)
    llm_calls_total: int = Field(ge=0)
    llm_fallbacks_total: int = Field(ge=0)


class AgentLatencyMetric(ContractModel):
    samples: int = Field(ge=0)
    p50_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)


class LLMTokenMetrics(ContractModel):
    prompt: int = Field(ge=0)
    completion: int = Field(ge=0)
    cost_inr: float = Field(ge=0)


class ComponentStatus(ContractModel):
    name: str
    channel: str
    risk: float = Field(ge=0, le=100)
    status: Band
    predicted: bool = False


class MachineSummary(ContractModel):
    id: str
    name: str
    type: str
    model: str
    line: str
    criticality: int = Field(ge=1, le=5)
    risk: float = Field(ge=0, le=100)
    band: Band
    status: MachineStatus
    quality: float = Field(ge=0, le=1)
    p_failure_24h: float = Field(ge=0, le=1)
    rul_days: float = Field(ge=0)
    updated_at: datetime


class WorkOrderSummary(ContractModel):
    workorder_id: str
    machine_id: str
    fault_code: str
    description: str
    status: Literal["open", "in_progress", "completed", "cancelled"]
    created_at: datetime
    completed_at: datetime | None = None


class FaultEventSummary(ContractModel):
    event_id: int = Field(gt=0)
    machine_id: str
    mode: str
    onset_ts: datetime
    failure_ts: datetime | None = None
    severity: Literal["low", "medium", "high", "critical"]
    actual_downtime_min: int = Field(ge=0)


class MachineDetail(MachineSummary):
    install_date: str
    rated_rpm: int = Field(gt=0)
    hourly_downtime_cost_inr: int = Field(gt=0)
    components: list[ComponentStatus]
    history: list[WorkOrderSummary]
    fault_events: list[FaultEventSummary]


class TelemetryPoint(ContractModel):
    ts: datetime
    channels: dict[str, float]
    quality_flags: list[str]


class TelemetryResponse(ContractModel):
    machine_id: str
    from_ts: datetime
    to_ts: datetime
    interval_seconds: int = Field(gt=0)
    channels: list[str]
    series: list[TelemetryPoint]


class FeatureContribution(ContractModel):
    name: str
    value: float
    contribution: float
    direction: Literal["increases_risk", "decreases_risk"]


class RulInterval(ContractModel):
    p10_days: float = Field(ge=0)
    p50_days: float = Field(ge=0)
    p90_days: float = Field(ge=0)


class PredictionResponse(ContractModel):
    prediction_id: str
    machine_id: str
    ts: datetime
    p_failure_24h: float = Field(ge=0, le=1)
    rul: RulInterval
    mode_probs: dict[str, float]
    top_features: list[FeatureContribution]
    risk: float = Field(ge=0, le=100)
    band: Band
    confidence: float = Field(ge=0, le=1)
    predictor: str
    model_version: str


class DiagnoseRequest(ContractModel):
    force_refresh: bool = False


class Citation(ContractModel):
    id: str
    kind: Literal["sensor", "work_order"]
    title: str
    detail: str
    channel: str | None = None
    t0: datetime | None = None
    t1: datetime | None = None
    workorder_id: str | None = None
    date: datetime | None = None
    fault_code: str | None = None
    technician_note: str | None = None
    cost_inr: float | None = Field(default=None, ge=0)


class RecommendedAction(ContractModel):
    action: str
    priority: Literal["high", "medium", "low"]
    rationale: str
    parts: list[str]
    est_labour_hours: float = Field(ge=0)
    window: str
    citations: list[str]


class DiagnosisResponse(ContractModel):
    diagnosis_id: str
    trace_id: str | None = None
    machine_id: str
    prediction_id: str
    fault_mode: str
    explanation: str
    confidence: float = Field(ge=0, le=1)
    reasoning: list[str]
    facts: list[str]
    assumptions: list[str]
    limits: list[str]
    actions: list[str]
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    risk: float = Field(default=0, ge=0, le=100)
    risk_terms: dict[str, float] = Field(default_factory=dict)
    citations: list[Citation]
    generated: bool
    offline_mode: bool = False
    model: str
    provider: str
    prompt_version: str
    generated_at: datetime
    fallback_reason: str | None = None


class DiagnoseResponse(ContractModel):
    trace_id: str
    diagnosis: DiagnosisResponse


class AgentRun(ContractModel):
    agent: str
    state: Literal["completed", "failed", "fallback"]
    latency_ms: float = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    model: str
    verdict: Literal["accepted", "rejected", "not_applicable"]
    error: str | None = None
    prompt: Any | None = None
    raw_response: Any | None = None
    guardrail_verdict: str | None = None


class RunResponse(ContractModel):
    trace_id: str
    status: Literal["completed", "failed", "running"]
    started_at: datetime
    completed_at: datetime | None
    runs: list[AgentRun]


class Alert(ContractModel):
    id: str
    machine_id: str
    kind: Literal["machine", "data"]
    severity: Band
    state: AlertState
    title: str
    message: str
    created_at: datetime
    updated_at: datetime


class AlertPatch(ContractModel):
    state: Literal["acknowledged", "dismissed"]


class ERPActionPayload(ContractModel):
    prediction_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=32)
    fault_code: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=500)
    priority: Literal["low", "medium", "high", "critical"]
    scheduled_start: datetime | None = None
    technician_notes: str = Field(default="", max_length=2000)
    parts: list[str] = Field(default_factory=list, max_length=50)
    labour_hours: float = Field(default=0, ge=0, le=1000)
    downtime_minutes: int = Field(default=0, ge=0, le=100000)
    cost_gbp: float = Field(default=0, ge=0, le=10_000_000)


class Approval(ContractModel):
    id: str
    prediction_id: str
    machine_id: str
    autonomy_level: Literal["A0", "A1", "A2", "A3"]
    required_role: Literal["technician", "planner", "admin"]
    state: ApprovalState
    decision_action: Literal["approve", "acknowledge"]
    action_payload: ERPActionPayload | None
    payload_hash: str | None
    actor: str | None
    reason: str | None
    decided_at: datetime | None
    erp_sync_status: Literal["not_requested", "pending_sync", "synced", "failed"] = "not_requested"
    erp_workorder_id: str | None = None


class ApprovalDecision(ContractModel):
    reason: str | None = Field(default=None, max_length=500)


class RejectRequest(ContractModel):
    reason: str = Field(min_length=1, max_length=500)


class ERPApprovalExecute(ContractModel):
    approval_id: str = Field(pattern=r"^approval-[1-9][0-9]*$", max_length=64)


class ERPAsset(ContractModel):
    asset_id: str
    name: str
    asset_type: str
    model: str
    criticality: int = Field(ge=1, le=5)
    cost_centre: str
    line: str
    status: Literal["operational", "degraded", "offline"]
    source: Literal["live", "cache"]


class ERPWorkOrder(ContractModel):
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
    labour_hours: float = Field(ge=0)
    downtime_minutes: int = Field(ge=0)
    cost_inr: float = Field(ge=0)
    lead_time_days: int = Field(ge=0)
    order_type: Literal["preventive", "corrective", "inspection"]


class ERPWorkOrderList(ContractModel):
    items: list[ERPWorkOrder]
    total: int = Field(ge=0)
    source: Literal["live", "cache"]
    degraded: bool
    detail: str


class ERPPart(ContractModel):
    part_id: str
    name: str
    fault_code: str
    in_stock: bool
    stock_quantity: int = Field(ge=0)
    lead_time_days: int = Field(ge=0)
    unit_cost_inr: float = Field(ge=0)


class ERPPartList(ContractModel):
    items: list[ERPPart]
    source: Literal["live", "cache"]
    degraded: bool
    detail: str


class ERPSyncItem(ContractModel):
    prediction_id: str
    status: Literal["pending", "failed"]
    attempts: int = Field(ge=0)
    last_error: str | None
    updated_at: datetime


class ERPStatus(ContractModel):
    status: Literal["healthy", "degraded"]
    source: Literal["live", "cache"]
    historical_workorders: int = Field(ge=0)
    pending_sync: int = Field(ge=0)
    failed_sync: int = Field(ge=0)
    queue: list[ERPSyncItem]
    detail: str
    generated_at: datetime


class ERPActionResponse(ContractModel):
    status: Literal["synced", "pending_sync"]
    prediction_id: str
    workorder_id: str | None
    message: str


class ScheduleWindow(ContractModel):
    id: str
    machine_id: str
    machine_name: str
    start: datetime
    end: datetime
    kind: Literal["predicted", "planned"]
    fault_mode: str
    confidence: float = Field(ge=0, le=1)
    status: Literal["recommended", "planned", "completed"]


class IngestPoint(ContractModel):
    machine_id: str
    ts: datetime
    channels: dict[str, float]


class IngestBatchRequest(ContractModel):
    points: list[IngestPoint] = Field(min_length=1, max_length=10000)


class IngestBatchResponse(ContractModel):
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    dlq_ids: list[str]


class DlqItem(ContractModel):
    id: str
    ts: datetime
    machine_id: str
    reason: str
    raw: dict[str, Any]


class LedgerPolicy(ContractModel):
    policy: Literal["reactive", "fixed_schedule", "predictive"]
    downtime_minutes: int = Field(ge=0)
    avoided_cost_inr: int = Field(ge=0)


class LedgerMissedFailure(ContractModel):
    event_id: str
    machine_id: str
    fault_mode: str
    severity: str
    onset_ts: datetime
    cost_inr: int = Field(ge=0)


class LedgerResponse(ContractModel):
    tau: float = Field(ge=0, le=1)
    target_reduction_pct: float = Field(ge=0)
    reduction_pct: float
    policies: list[LedgerPolicy]
    missed_failures: list[LedgerMissedFailure] = Field(default_factory=list)


class EvalMetric(ContractModel):
    name: str
    value: float
    target: float | None
    passed: bool
    sample_size: int = Field(ge=0)


class EvalReportResponse(ContractModel):
    generated_at: datetime
    model_version: str
    prompt_version: str = "unknown"
    run_mode: str = "unknown"
    metrics: list[EvalMetric]
    notes: list[str]
    summary: dict[str, Any] = Field(default_factory=dict)


class LlmOfflineToggleRequest(ContractModel):
    enabled: bool


class LlmOfflineResponse(ContractModel):
    enabled: bool
    effective_offline: bool
    mode: Literal["template", "gemini"]
    model: str
    provider: str


class AuditEntry(ContractModel):
    seq: int = Field(ge=1)
    ts: datetime
    actor: str
    action: str
    subject: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class AuditVerifyResponse(ContractModel):
    intact: bool
    entries: int = Field(ge=0)
    checked_entries: int = Field(ge=0)
    first_invalid_seq: int | None


class QuarantineEntry(ContractModel):
    quarantine_id: str
    workorder_id: str
    machine_id: str
    status: Literal["quarantined", "released"]
    patterns: list[str]
    text: str
    audit_seq: int = Field(ge=1)
    detected_at: datetime
    released_at: datetime | None
    released_by: str | None
    release_reason: str | None


class QuarantineReleaseRequest(ContractModel):
    reason: str = Field(min_length=1, max_length=500)


class QuarantineReleaseResponse(ContractModel):
    quarantine_id: str
    status: Literal["released"]
    reason: str
    released_by: str
    released_at: datetime


class RehearsalInjectRequest(ContractModel):
    machine_id: str
    mode: RehearsalMode
    severity: Literal["low", "medium", "high", "critical"]
    onset: datetime


class RehearsalInjectResponse(ContractModel):
    event_id: str
    machine_id: str
    status: Literal["injected"]


class RehearsalResetResponse(ContractModel):
    restored_machines: int = Field(ge=0)
    status: Literal["reset"]


class ScoreboardItem(ContractModel):
    scenario: str
    machine_id: str
    expected: str
    observed: str
    passed: bool
    detected: bool = False
    lead_time_hours: float | None = Field(default=None, ge=0)
    mode_correct: bool = False


class RehearsalScoreboardResponse(ContractModel):
    scenarios: list[ScoreboardItem]
    all_passed: bool
    detection_rate: float = Field(default=0.0, ge=0, le=1)
    lead_time_hours: float | None = Field(default=None, ge=0)
    mode_accuracy: float = Field(default=0.0, ge=0, le=1)


class StreamEvent(ContractModel):
    id: str
    event: Literal["telemetry", "alert", "run_state"]
    data: dict[str, Any]


class MetaResponse(ContractModel):
    bands: dict[str, int]
    modes: list[str]
    weights: dict[str, int]
    versions: dict[str, str]
    feature_flags: dict[str, bool]
