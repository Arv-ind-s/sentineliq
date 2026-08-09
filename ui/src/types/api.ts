/** Frozen mirror of api/schemas.py. Dates are ISO-8601 strings over HTTP. */

export type Band = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "NODATA";
export type MachineStatus = "online" | "degraded" | "offline";
export type AlertState = "open" | "acknowledged" | "dismissed";
export type ApprovalState = "pending" | "approved" | "acknowledged" | "rejected" | "superseded";
export type UserRole = "operator" | "technician" | "planner" | "admin";
export type Timestamp = string;

export interface ErrorBody { code: string; message: string; trace_id: string; }
export interface ErrorResponse { error: ErrorBody; }
export interface LoginRequest { username: string; password: string; }
export interface LoginResponse { username: string; role: UserRole; expires_at: Timestamp; can_switch_persona: boolean; }
export interface CurrentUserResponse { username: string; role: UserRole; can_switch_persona: boolean; }
export interface MessageResponse { message: string; }
export interface DependencyStatus { name: string; status: "up" | "degraded" | "down"; latency_ms: number; detail: string; breaker_state: "closed" | "open" | "half_open"; tls_verified: boolean; reachable: boolean | null; model_name: string | null; provider: string | null; }
export interface HealthResponse { status: "healthy" | "degraded" | "unhealthy"; dependencies: DependencyStatus[]; replay: "running" | "paused" | "stopped"; model_version: string; generated_at: Timestamp; }
export interface AgentLatencyMetric { samples: number; p50_ms: number; p95_ms: number; }
export interface LLMTokenMetrics { prompt: number; completion: number; cost_inr: number; }
export interface MetricsResponse { window: string; requests: number; errors_by_code: Record<string, number>; agent_latencies: Record<string, AgentLatencyMetric>; guardrail_rejections_by_rule: Record<string, number>; llm_tokens: LLMTokenMetrics; dlq_depth: number; circuit_breaker_trips: Record<string, number>; requests_total: number; diagnoses_total: number; alerts_open: number; prediction_latency_p95_ms: number; llm_calls_total: number; llm_fallbacks_total: number; }
  export interface ComponentStatus { name: string; channel: string; risk: number; status: Band; predicted: boolean; }
export interface MachineSummary { id: string; name: string; type: string; model: string; line: string; criticality: number; risk: number; band: Band; status: MachineStatus; quality: number; p_failure_24h: number; rul_days: number; updated_at: Timestamp; }
export interface WorkOrderSummary { workorder_id: string; machine_id: string; fault_code: string; description: string; status: "open" | "in_progress" | "completed" | "cancelled"; created_at: Timestamp; completed_at: Timestamp | null; }
export interface FaultEventSummary { event_id: number; machine_id: string; mode: string; onset_ts: Timestamp; failure_ts: Timestamp | null; severity: "low" | "medium" | "high" | "critical"; actual_downtime_min: number; }
export interface MachineDetail extends MachineSummary { install_date: string; rated_rpm: number; hourly_downtime_cost_inr: number; components: ComponentStatus[]; history: WorkOrderSummary[]; fault_events: FaultEventSummary[]; }
export interface TelemetryPoint { ts: Timestamp; channels: Record<string, number>; quality_flags: string[]; }
export interface TelemetryResponse { machine_id: string; from_ts: Timestamp; to_ts: Timestamp; interval_seconds: number; channels: string[]; series: TelemetryPoint[]; }
export interface FeatureContribution { name: string; value: number; contribution: number; direction: "increases_risk" | "decreases_risk"; }
export interface RulInterval { p10_days: number; p50_days: number; p90_days: number; }
export interface PredictionResponse { prediction_id: string; machine_id: string; ts: Timestamp; p_failure_24h: number; rul: RulInterval; mode_probs: Record<string, number>; top_features: FeatureContribution[]; risk: number; band: Band; confidence: number; predictor: string; model_version: string; }
export interface DiagnoseRequest { force_refresh?: boolean; }
export interface Citation { id: string; kind: "sensor" | "work_order"; title: string; detail: string; channel?: string | null; t0?: Timestamp | null; t1?: Timestamp | null; workorder_id?: string | null; date?: Timestamp | null; fault_code?: string | null; technician_note?: string | null; cost_inr?: number | null; }
export interface RecommendedAction { action: string; priority: "high" | "medium" | "low"; rationale: string; parts: string[]; est_labour_hours: number; window: string; citations: string[]; }
export interface DiagnosisResponse { diagnosis_id: string; trace_id?: string | null; machine_id: string; prediction_id: string; fault_mode: string; explanation: string; confidence: number; reasoning: string[]; facts: string[]; assumptions: string[]; limits: string[]; actions: string[]; recommended_actions: RecommendedAction[]; risk: number; risk_terms: Record<string, number>; citations: Citation[]; generated: boolean; offline_mode: boolean; model: string; provider: string; prompt_version: string; generated_at: Timestamp; fallback_reason?: string | null; }
export interface DiagnoseResponse { trace_id: string; diagnosis: DiagnosisResponse; }
export interface AgentRun { agent: string; state: "completed" | "failed" | "fallback"; latency_ms: number; prompt_tokens: number; completion_tokens: number; model: string; verdict: "accepted" | "rejected" | "not_applicable"; error: string | null; guardrail_verdict?: string | null; prompt?: unknown; raw_response?: unknown; }
export interface RunResponse { trace_id: string; status: "completed" | "failed" | "running"; started_at: Timestamp; completed_at: Timestamp | null; runs: AgentRun[]; }
export interface Alert { id: string; machine_id: string; kind: "machine" | "data"; severity: Band; state: AlertState; title: string; message: string; created_at: Timestamp; updated_at: Timestamp; }
export interface AlertPatch { state: "acknowledged" | "dismissed"; }
export interface ERPActionPayload { prediction_id: string; asset_id: string; fault_code: string; description: string; priority: "low" | "medium" | "high" | "critical"; scheduled_start: Timestamp | null; technician_notes: string; parts: string[]; labour_hours: number; downtime_minutes: number; cost_gbp: number; }
export interface Approval { id: string; prediction_id: string; machine_id: string; autonomy_level: "A0" | "A1" | "A2" | "A3"; required_role: "technician" | "planner" | "admin"; state: ApprovalState; decision_action: "approve" | "acknowledge"; action_payload: ERPActionPayload | null; payload_hash: string | null; actor: string | null; reason: string | null; decided_at: Timestamp | null; erp_sync_status: "not_requested" | "pending_sync" | "synced" | "failed"; erp_workorder_id: string | null; }
export interface ApprovalDecision { reason?: string | null; }
export interface RejectRequest { reason: string; }
export interface ScheduleWindow { id: string; machine_id: string; machine_name: string; start: Timestamp; end: Timestamp; kind: "predicted" | "planned"; fault_mode: string; confidence: number; status: "recommended" | "planned" | "completed"; }
export interface ERPAsset { asset_id: string; name: string; asset_type: string; model: string; criticality: number; cost_centre: string; line: string; status: "operational" | "degraded" | "offline"; source: "live" | "cache"; }
export interface ERPWorkOrder { workorder_id: string; asset_id: string; fault_code: string; description: string; priority: "low" | "medium" | "high" | "critical"; status: "open" | "in_progress" | "completed" | "cancelled"; created_at: Timestamp; updated_at: Timestamp; scheduled_start: Timestamp | null; completed_at: Timestamp | null; technician_notes: string; parts: string[]; labour_hours: number; downtime_minutes: number; cost_inr: number; lead_time_days: number; order_type: "preventive" | "corrective" | "inspection"; }
export interface ERPWorkOrderList { items: ERPWorkOrder[]; total: number; source: "live" | "cache"; degraded: boolean; detail: string; }
export interface ERPPart { part_id: string; name: string; fault_code: string; in_stock: boolean; stock_quantity: number; lead_time_days: number; unit_cost_inr: number; }
export interface ERPPartList { items: ERPPart[]; source: "live" | "cache"; degraded: boolean; detail: string; }
export interface ERPSyncItem { prediction_id: string; status: "pending" | "failed"; attempts: number; last_error: string | null; updated_at: Timestamp; }
export interface ERPStatus { status: "healthy" | "degraded"; source: "live" | "cache"; historical_workorders: number; pending_sync: number; failed_sync: number; queue: ERPSyncItem[]; detail: string; generated_at: Timestamp; }
export interface ERPApprovalExecute { approval_id: string; }
export interface ERPActionResponse { status: "synced" | "pending_sync"; prediction_id: string; workorder_id: string | null; message: string; }
export interface IngestPoint { machine_id: string; ts: Timestamp; channels: Record<string, number>; }
export interface IngestBatchRequest { points: IngestPoint[]; }
export interface IngestBatchResponse { accepted: number; rejected: number; dlq_ids: string[]; }
export interface DlqItem { id: string; ts: Timestamp; machine_id: string; reason: string; raw: Record<string, unknown>; }
export interface LedgerPolicy { policy: "reactive" | "fixed_schedule" | "predictive"; downtime_minutes: number; avoided_cost_inr: number; }
export interface LedgerMissedFailure { event_id: string; machine_id: string; fault_mode: string; severity: string; onset_ts: Timestamp; cost_inr: number; }
export interface LedgerResponse { tau: number; target_reduction_pct: number; reduction_pct: number; policies: LedgerPolicy[]; missed_failures: LedgerMissedFailure[]; }
export interface EvalMetric { name: string; value: number; target: number | null; passed: boolean; sample_size: number; }
export interface EvalInjectionNote { workorder_id: string; pattern: string; matched: boolean; matched_patterns: string[]; expected: string; }
export interface EvalScenario { id: string; kind: string; passed: boolean; guardrail_accepted: boolean; }
export interface EvalSummary {
  model?: string;
  run_mode?: string;
  reliability?: { runs: number; completed: number; unhandled: number; };
  latency?: { p50_ms: number; p95_ms: number; };
  injection?: { total: number; matched: number; quarantined: number; known_evasions: number; notes: EvalInjectionNote[]; };
  hallucination?: { scenarios: EvalScenario[]; };
}
export interface EvalReportResponse { generated_at: Timestamp; model_version: string; prompt_version?: string; run_mode?: string; metrics: EvalMetric[]; notes: string[]; summary?: EvalSummary; }
export interface LlmOfflineResponse { enabled: boolean; effective_offline: boolean; mode: "template" | "gateway"; model: string; provider: string; }
export interface AuditEntry { seq: number; ts: Timestamp; actor: string; action: string; subject: string; payload: Record<string, unknown>; prev_hash: string; hash: string; }
export interface AuditVerifyResponse { intact: boolean; entries: number; checked_entries: number; first_invalid_seq: number | null; }
export interface QuarantineEntry { quarantine_id: string; workorder_id: string; machine_id: string; status: "quarantined" | "released"; patterns: string[]; text: string; audit_seq: number; detected_at: Timestamp; released_at: Timestamp | null; released_by: string | null; release_reason: string | null; }
export interface QuarantineReleaseResponse { quarantine_id: string; status: "released"; reason: string; released_by: string; released_at: Timestamp; }
export type RehearsalMode = "bearing_wear" | "rotor_imbalance" | "seal_leak" | "motor_overheat" | "lubrication_degradation" | "sensor_drift";
export type RehearsalSeverity = "low" | "medium" | "high" | "critical";
export interface RehearsalInjectRequest { machine_id: string; mode: RehearsalMode; severity: RehearsalSeverity; onset: Timestamp; }
export interface RehearsalInjectResponse { event_id: string; machine_id: string; status: "injected"; }
export interface RehearsalResetResponse { restored_machines: number; status: "reset"; }
export interface ScoreboardItem { scenario: string; machine_id: string; expected: string; observed: string; passed: boolean; detected: boolean; lead_time_hours: number | null; mode_correct: boolean; }
export interface RehearsalScoreboardResponse { scenarios: ScoreboardItem[]; all_passed: boolean; detection_rate: number; lead_time_hours: number | null; mode_accuracy: number; }
export interface StreamEvent { id: string; event: "telemetry" | "alert" | "run_state"; data: Record<string, unknown>; }
export interface MetaResponse { bands: Record<string, number>; modes: string[]; weights: Record<string, number>; versions: Record<string, string>; feature_flags: Record<string, boolean>; }
