"""SQLAlchemy 2.0 models for the SentinelPdM plant database."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative root for the dialect-neutral plant schema."""


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    line: Mapped[str] = mapped_column(String(32), nullable=False)
    install_date: Mapped[int] = mapped_column(BigInteger, nullable=False)
    criticality: Mapped[int] = mapped_column(Integer, nullable=False)
    rated_rpm: Mapped[int] = mapped_column(Integer, nullable=False)
    hourly_downtime_cost_gbp: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_map: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)


class FaultEvent(Base):
    __tablename__ = "fault_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    onset_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failure_ts: Mapped[int | None] = mapped_column(BigInteger)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    actual_downtime_min: Mapped[int] = mapped_column(Integer, nullable=False)


class WorkOrder(Base):
    __tablename__ = "work_orders"

    workorder_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), nullable=False)
    fault_code: Mapped[str] = mapped_column(String(32), nullable=False)
    technician_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parts_used: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    labour_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    downtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_gbp: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_type: Mapped[str] = mapped_column(String(24), nullable=False, default="corrective")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed")
    created_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_ts: Mapped[int | None] = mapped_column(BigInteger)


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("machine_id", "window_end", name="uq_features_machine_window"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), nullable=False)
    window_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fv: Mapped[str] = mapped_column(String(16), nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("machine_id", "ts", "model_version", name="uq_prediction_window_model"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), nullable=False)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    p_failure: Mapped[float] = mapped_column(Float, nullable=False)
    rul_p10: Mapped[float] = mapped_column(Float, nullable=False)
    rul_p50: Mapped[float] = mapped_column(Float, nullable=False)
    rul_p90: Mapped[float] = mapped_column(Float, nullable=False)
    mode_probs: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    risk: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    predictor: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="serving")


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("prediction_id", "citation_id", name="uq_evidence_prediction_citation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"), nullable=False)
    citation_id: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"), nullable=False)
    diagnosis: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    facts: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    limits: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    actions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    machine_id: Mapped[str | None] = mapped_column(ForeignKey("machines.id"))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000))
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_gbp: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    machine_id: Mapped[str] = mapped_column(ForeignKey("machines.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"), nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(8), nullable=False)
    required_role: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[int | None] = mapped_column(BigInteger)
    request_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    action_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    erp_sync_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="not_requested",
        server_default="not_requested",
    )
    erp_workorder_id: Mapped[str | None] = mapped_column(String(64))


class AuditLedger(Base):
    __tablename__ = "audit_ledger"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


class IngestDlq(Base):
    __tablename__ = "ingest_dlq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    machine_id: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    argon2_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
