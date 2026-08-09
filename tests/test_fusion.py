from __future__ import annotations

import pytest

from core.fusion import HistoryEvidence, MachineContext, SensorEvidence, fuse
from core.config import settings


def sensor(**overrides: object) -> SensorEvidence:
    values: dict[str, object] = {
        "p_failure_24h": 0.80,
        "rul_p50_days": 7.0,
        "predicted_mode": "bearing_wear",
        "data_quality": 0.95,
        "evidence_count": 3,
    }
    values.update(overrides)
    return SensorEvidence(**values)  # type: ignore[arg-type]


def history(**overrides: object) -> HistoryEvidence:
    values: dict[str, object] = {"history_prior": 0.10, "dominant_mode": "bearing_wear"}
    values.update(overrides)
    return HistoryEvidence(**values)  # type: ignore[arg-type]


def machine(**overrides: object) -> MachineContext:
    values: dict[str, object] = {"criticality": "major", "hourly_downtime_cost_gbp": 1800}
    values.update(overrides)
    return MachineContext(**values)  # type: ignore[arg-type]


def test_risk_formula_has_five_named_terms_that_sum_to_risk() -> None:
    decision = fuse(sensor(), history(), machine())
    terms = decision.explain()

    assert set(terms) == {"p_failure_24h", "rul_pressure", "history_prior", "criticality_weight", "data_quality"}
    assert sum(terms.values()) == pytest.approx(decision.risk, abs=0.01)
    assert decision.prediction_status == "PREDICTION"
    assert decision.band in {"MEDIUM", "HIGH", "CRITICAL"}
    assert decision.confidence > 0


def test_insufficient_data_returns_data_alert_and_no_prediction() -> None:
    decision = fuse(sensor(data_quality=0.62), history(), machine())

    assert decision.prediction_status == "NO_PREDICTION"
    assert decision.action_class == "NO_PREDICTION"
    assert decision.band == "NODATA"
    assert "insufficient_data" in decision.hard_rules
    assert len(decision.alerts) == 1
    assert decision.alerts[0].kind == "data"
    assert decision.alerts[0].kind != "machine"


def test_safety_critical_mode_floors_band_and_forces_a0() -> None:
    decision = fuse(sensor(p_failure_24h=0.10, rul_p50_days=30, predicted_mode="motor_overheat", data_quality=1.0), history(dominant_mode="motor_overheat"), machine(criticality=5))

    assert "safety_critical_mode" in decision.hard_rules
    assert decision.band == "CRITICAL"
    assert decision.autonomy == "A0"
    assert decision.requires_approval is True
    assert decision.approver_role == "planner"


def test_repeat_failure_adds_configured_risk_and_escalates_root_cause() -> None:
    normal = fuse(sensor(), history(), machine())
    repeated = fuse(sensor(), history(repeat_failure=True), machine())
    repeated_from_window = fuse(sensor(), history(fault_code_occurrences_days=(10.0, 90.0)), machine())

    assert "repeat_failure" in repeated.hard_rules
    assert repeated.risk == pytest.approx(normal.risk + settings.fusion.rules.repeat_failure_risk_add, abs=0.01)
    assert repeated.root_cause_escalation is True
    assert "repeat_failure" in repeated_from_window.hard_rules


def test_evidence_conflict_reduces_confidence_and_requires_disagreement() -> None:
    agreeing = fuse(sensor(), history(dominant_mode="bearing_wear"), machine())
    conflicting = fuse(sensor(), history(dominant_mode="seal_leak"), machine())

    assert conflicting.agreement == "conflict"
    assert "evidence_conflict" in conflicting.hard_rules
    assert conflicting.must_state_disagreement is True
    assert conflicting.confidence == pytest.approx(agreeing.confidence * settings.fusion.rules.evidence_conflict_factor, abs=0.01)
    assert conflicting.autonomy == "A0"


def test_sensor_drift_switches_action_to_instrumentation_verification() -> None:
    decision = fuse(sensor(predicted_mode="sensor_drift", sensor_drift_signal=True), history(dominant_mode=None, transducer_replacements_90d=2), machine())

    assert "sensor_drift_suspected" in decision.hard_rules
    assert decision.action_class == "verify_instrumentation"
    assert decision.prediction_status == "PREDICTION"


def test_autonomy_ladder_and_cost_gate_are_configured() -> None:
    low = fuse(sensor(p_failure_24h=0.10, rul_p50_days=30), history(), machine(criticality="minor"))
    medium = fuse(sensor(p_failure_24h=0.90, rul_p50_days=30), history(), machine(criticality="minor"))
    high = fuse(sensor(p_failure_24h=0.99, rul_p50_days=30), history(), machine(criticality="minor"))
    expensive = fuse(sensor(p_failure_24h=0.90, rul_p50_days=30), history(), machine(criticality="minor", hourly_downtime_cost_gbp=5001))

    assert low.autonomy == "A3"
    assert medium.autonomy == "A2"
    assert high.autonomy == "A1"
    assert expensive.autonomy == "A0"
    assert expensive.requires_approval is True
