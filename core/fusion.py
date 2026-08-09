"""Deterministic evidence fusion for risk, urgency, and autonomy routing.

This module has no LLM or persistence dependency.  It converts the numeric
prediction, maintenance-history summary, and machine metadata into a fixed
decision before any narrative agent runs.  Inputs are explicit dataclasses,
while mappings are accepted at the boundary to keep agent hand-offs simple.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from core.config import FusionConfig, settings


Band = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "NODATA"]
Agreement = Literal["agree", "partial", "conflict"]
Autonomy = Literal["A0", "A1", "A2", "A3"]
AlertKind = Literal["machine", "data"]


@dataclass(frozen=True)
class SensorEvidence:
    """Numeric output from the predictor and preprocessing quality report."""

    p_failure_24h: float
    rul_p50_days: float
    predicted_mode: str
    data_quality: float
    evidence_count: int
    required_channel_suspect: bool = False
    sensor_drift_signal: bool | None = None


@dataclass(frozen=True)
class HistoryEvidence:
    """Structured maintenance evidence supplied by the retrieval agent."""

    history_prior: float = 0.0
    dominant_mode: str | None = None
    repeat_failure: bool = False
    repeat_failure_count: int = 0
    fault_code_occurrences_days: tuple[float, ...] = ()
    transducer_replacements_90d: int = 0


@dataclass(frozen=True)
class MachineContext:
    """Machine facts needed for criticality and approval routing."""

    criticality: int | str
    hourly_downtime_cost_gbp: float = 0.0


@dataclass(frozen=True)
class RiskTerm:
    name: str
    contribution: float


@dataclass(frozen=True)
class FusionAlert:
    kind: AlertKind
    code: str
    message: str


@dataclass(frozen=True)
class FusionDecision:
    risk: float
    band: Band
    agreement: Agreement
    confidence: float
    autonomy: Autonomy
    action_class: str
    prediction_status: Literal["PREDICTION", "NO_PREDICTION"]
    hard_rules: tuple[str, ...]
    alerts: tuple[FusionAlert, ...]
    terms: tuple[RiskTerm, ...]
    must_state_disagreement: bool = False
    root_cause_escalation: bool = False
    requires_approval: bool = False
    approver_role: str | None = None

    def explain(self) -> dict[str, float]:
        """Return the five named effective contributions used by the UI."""
        return explain(self)


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _sensor(value: SensorEvidence | Mapping[str, Any]) -> SensorEvidence:
    return value if isinstance(value, SensorEvidence) else SensorEvidence(
        p_failure_24h=float(_field(value, "p_failure_24h", 0.0)),
        rul_p50_days=float(_field(value, "rul_p50_days", _field(value, "rul_days", 0.0))),
        predicted_mode=str(_field(value, "predicted_mode", _field(value, "mode", "unknown"))),
        data_quality=float(_field(value, "data_quality", _field(value, "quality", 0.0))),
        evidence_count=int(_field(value, "evidence_count", 0)),
        required_channel_suspect=bool(_field(value, "required_channel_suspect", False)),
        sensor_drift_signal=_field(value, "sensor_drift_signal", None),
    )


def _history(value: HistoryEvidence | Mapping[str, Any] | None) -> HistoryEvidence:
    if value is None:
        return HistoryEvidence()
    return value if isinstance(value, HistoryEvidence) else HistoryEvidence(
        history_prior=float(_field(value, "history_prior", 0.0)),
        dominant_mode=_field(value, "dominant_mode", None),
        repeat_failure=bool(_field(value, "repeat_failure", False)),
        repeat_failure_count=int(_field(value, "repeat_failure_count", 0)),
        fault_code_occurrences_days=tuple(float(item) for item in _field(value, "fault_code_occurrences_days", ())),
        transducer_replacements_90d=int(_field(value, "transducer_replacements_90d", 0)),
    )


def _machine(value: MachineContext | Mapping[str, Any] | None) -> MachineContext:
    if value is None:
        return MachineContext(criticality="minor")
    return value if isinstance(value, MachineContext) else MachineContext(
        criticality=_field(value, "criticality", "minor"),
        hourly_downtime_cost_gbp=float(_field(value, "hourly_downtime_cost_gbp", _field(value, "cost_gbp", 0.0))),
    )


def _criticality_level(value: int | str, thresholds: Mapping[str, int]) -> str:
    """Map API integer criticality 1–5 to the spec's three named levels."""
    if isinstance(value, str):
        level = value.lower()
        if level in {"critical", "major", "minor"}:
            return level
        try:
            value = int(value)
        except ValueError:
            return "minor"
    return "critical" if value >= thresholds["critical"] else "major" if value >= thresholds["major"] else "minor"


def _band(risk: float, config: FusionConfig) -> Band:
    bands = config.bands
    if risk >= bands["critical"]:
        return "CRITICAL"
    if risk >= bands["high"]:
        return "HIGH"
    if risk >= bands["medium"]:
        return "MEDIUM"
    return "LOW"


def _effective_terms(raw_terms: dict[str, float], target_risk: float) -> tuple[RiskTerm, ...]:
    raw_total = sum(raw_terms.values())
    if raw_total > 0:
        scale = target_risk / raw_total
        values = {name: contribution * scale for name, contribution in raw_terms.items()}
    else:
        values = {name: 0.0 for name in raw_terms}
        values["criticality_weight"] = target_risk
    return tuple(RiskTerm(name=name, contribution=round(values[name], 8)) for name in raw_terms)


def _autonomy(
    band: Band,
    confidence: float,
    machine: MachineContext,
    agreement: Agreement,
    safety_critical: bool,
    config: FusionConfig,
) -> Autonomy:
    if band == "NODATA" or band == "CRITICAL" or confidence < config.autonomy.a0_confidence_below:
        return "A0"
    if agreement == "conflict" or safety_critical or machine.hourly_downtime_cost_gbp > config.autonomy.a0_cost_above_gbp:
        return "A0"
    if band == "HIGH":
        return "A1"
    if band == "MEDIUM":
        return "A2"
    return "A3"


def fuse(
    sensor: SensorEvidence | Mapping[str, Any],
    history: HistoryEvidence | Mapping[str, Any] | None = None,
    machine: MachineContext | Mapping[str, Any] | None = None,
    *,
    config: FusionConfig | None = None,
) -> FusionDecision:
    """Fuse evidence into a total, deterministic decision.

    ``config`` is injectable for tests and controlled deployments; callers
    normally use the validated application settings.  Malformed numeric input
    is bounded at this boundary so fusion remains total and fail-closed.
    """
    fusion_config = config or settings.fusion
    sensor_evidence = _sensor(sensor)
    history_evidence = _history(history)
    machine_context = _machine(machine)
    quality = _clamp(sensor_evidence.data_quality, 0.0, 1.0)
    probability = _clamp(sensor_evidence.p_failure_24h, 0.0, 1.0)
    rul_days = max(0.0, sensor_evidence.rul_p50_days)
    mode = sensor_evidence.predicted_mode.strip().lower()
    rules_config = fusion_config.rules
    criticality = _criticality_level(machine_context.criticality, rules_config.criticality_score_thresholds)
    hard_rules: list[str] = []
    alerts: list[FusionAlert] = []

    insufficient = quality < rules_config.min_completeness or sensor_evidence.required_channel_suspect
    if insufficient:
        hard_rules.append("insufficient_data")
        alerts.append(FusionAlert("data", "insufficient_data", "Telemetry quality is insufficient; no failure prediction was issued."))

    dominant_mode = (history_evidence.dominant_mode or "").strip().lower()
    agreement: Agreement = "partial" if not dominant_mode else "agree" if dominant_mode == mode else "conflict"
    if agreement == "conflict":
        hard_rules.append("evidence_conflict")

    recent_occurrences = sum(
        0.0 <= age <= rules_config.repeat_failure_window_days
        for age in history_evidence.fault_code_occurrences_days
    )
    repeated = (
        history_evidence.repeat_failure
        or history_evidence.repeat_failure_count >= rules_config.repeat_failure_min_count
        or recent_occurrences >= rules_config.repeat_failure_min_count
    )
    if repeated:
        hard_rules.append("repeat_failure")

    sensor_drift_signal = sensor_evidence.sensor_drift_signal if sensor_evidence.sensor_drift_signal is not None else mode == "sensor_drift"
    sensor_drift = bool(sensor_drift_signal) and history_evidence.transducer_replacements_90d >= rules_config.sensor_drift_min_replacements
    if sensor_drift:
        hard_rules.append("sensor_drift_suspected")

    safety_critical = mode in fusion_config.autonomy.safety_critical_modes and criticality == "critical"
    if safety_critical:
        hard_rules.append("safety_critical_mode")

    if insufficient:
        terms = _effective_terms({name: 0.0 for name in ("p_failure_24h", "rul_pressure", "history_prior", "criticality_weight", "data_quality")}, 0.0)
        return FusionDecision(
            risk=0.0,
            band="NODATA",
            agreement=agreement,
            confidence=0.0,
            autonomy="A0",
            action_class="NO_PREDICTION",
            prediction_status="NO_PREDICTION",
            hard_rules=tuple(hard_rules),
            alerts=tuple(alerts),
            terms=terms,
            must_state_disagreement=agreement == "conflict",
            root_cause_escalation=repeated,
            requires_approval=True,
            approver_role="planner",
        )

    weights = fusion_config.weights
    rul_pressure = _clamp(1.0 - rul_days / rules_config.rul_pressure_days, 0.0, 1.0)
    criticality_weight = _clamp(rules_config.criticality_weights[criticality], 0.0, 1.0)
    raw_terms = {
        "p_failure_24h": weights["p_failure"] * probability,
        "rul_pressure": weights["rul_pressure"] * rul_pressure,
        "history_prior": weights["history"] * _clamp(history_evidence.history_prior, 0.0, 1.0) + (rules_config.repeat_failure_risk_add if repeated else 0),
        "criticality_weight": weights["criticality"] * criticality_weight,
        "data_quality": weights["data_quality"] * (1.0 - quality),
    }
    base_risk = _clamp(sum(raw_terms.values()), 0.0, 100.0)
    risk = max(base_risk, float(fusion_config.bands["critical"])) if safety_critical else base_risk
    risk = _clamp(risk, 0.0, 100.0)
    terms = _effective_terms(raw_terms, risk)

    calibrated_prob_margin = _clamp(
        abs(probability - rules_config.confidence_probability_midpoint) * rules_config.confidence_probability_scale,
        0.0,
        1.0,
    )
    agreement_factor = _clamp(rules_config.agreement_factors[agreement], 0.0, 1.0)
    evidence_factor = _clamp(sensor_evidence.evidence_count / rules_config.evidence_count_saturation, 0.0, 1.0)
    confidence = calibrated_prob_margin * quality * agreement_factor * evidence_factor
    if agreement == "conflict":
        confidence *= rules_config.evidence_conflict_factor
    confidence = _clamp(confidence, 0.0, 1.0)
    band = "CRITICAL" if safety_critical else _band(risk, fusion_config)
    autonomy = _autonomy(band, confidence, machine_context, agreement, safety_critical, fusion_config)
    action_class = "verify_instrumentation" if sensor_drift else "monitor" if band == "LOW" else "repair"
    return FusionDecision(
        risk=round(risk, 8),
        band=band,
        agreement=agreement,
        confidence=round(confidence, 8),
        autonomy=autonomy,
        action_class=action_class,
        prediction_status="PREDICTION",
        hard_rules=tuple(hard_rules),
        alerts=tuple(alerts),
        terms=terms,
        must_state_disagreement=agreement == "conflict",
        root_cause_escalation=repeated,
        requires_approval=autonomy == "A0",
        approver_role="planner" if autonomy == "A0" else "technician" if autonomy in {"A1", "A2"} else None,
    )


def explain(decision: FusionDecision) -> dict[str, float]:
    """Return the five risk contributions; their sum equals ``decision.risk``."""
    return {term.name: term.contribution for term in decision.terms}


__all__ = [
    "FusionAlert",
    "FusionDecision",
    "HistoryEvidence",
    "MachineContext",
    "RiskTerm",
    "SensorEvidence",
    "explain",
    "fuse",
]
