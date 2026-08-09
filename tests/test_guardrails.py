from __future__ import annotations

from pathlib import Path

import pytest

from agents.base import RunContext, run_agent
from agents.guardrail_agent import GuardrailAgent
from core.guardrails import DiagnosisDraft, Guardrail, load_rejections, normalise_numeric, run_guardrails


EVIDENCE = {
    "confidence": 0.87,
    "rul_days": 4.2,
    "temperature_c": 78.4,
    "cost_gbp": 4200.0,
    "risk": 62.5,
    "sensor": {"S1": {"value": 0.87}, "S2": {"value": 78.4}},
    "maintenance": {"M1": {"cost_gbp": 4200.0}, "M3": {"cost_gbp": 4200.0}},
}


def valid_draft() -> dict[str, object]:
    return {
        "fault_diagnosis": "Bearing degradation predicted [S1][M1]",
        "reasoning_steps": ["The current window supports the diagnosis. [S1]", "History is consistent. [M1]"],
        "facts": ["Confidence is 87% [S1]", "RUL is 4.2 days [S1]", "Temperature is 173.1 °F [S2]"],
        "assumptions": ["The current operating load remains representative."],
        "recommended_actions": [{
            "action": "Inspect the drive-end bearing",
            "priority": "high",
            "rationale": "The repair cost is £4,200. [M1]",
            "parts": ["Bearing kit"],
            "est_labour_hours": 2.0,
            "window": "Next planned window",
            "citations": ["M1"],
        }],
        "limits": "The draft cannot confirm internal bearing damage.",
        "citations": ["S1", "S2", "M1"],
    }


def check(text: str, tmp_path: Path):
    draft = valid_draft()
    draft["reasoning_steps"] = [f"{text} [S1]"]
    return run_guardrails(draft, EVIDENCE, trace_id=f"trace-{text[:4]}", database_url=f"sqlite:///{tmp_path / 'plant.db'}")


def test_normalise_numeric_emits_all_required_representations() -> None:
    quantities = normalise_numeric(EVIDENCE)
    representations = {(dimension, round(value, 3)) for quantity in quantities for dimension, value in quantity.representations}

    assert ("probability", 0.87) in representations
    assert ("probability", 87.0) in representations
    assert ("duration", 4.2) in representations
    assert ("duration", 100.8) in representations
    assert ("temperature", 173.12) in representations
    assert ("cost", 4.2) in representations
    assert ("cost", 4200.0) in representations


@pytest.mark.parametrize("text", ["confidence is 87%", "confidence is 0.87"])
def test_probability_round_trip_is_grounded(text: str, tmp_path: Path) -> None:
    result = check(text, tmp_path)

    assert result.accepted is True
    assert result.rejections == ()


@pytest.mark.parametrize("text", ["RUL is 101 h", "RUL is 4.2 d", "temperature is 173.1 °F", "temperature is 78.4 °C", "cost is 4.2k", "cost is £4,200"])
def test_unit_and_scale_round_trips_are_grounded(text: str, tmp_path: Path) -> None:
    result = check(text, tmp_path)

    assert result.accepted is True
    assert not result.rejections


def test_indian_grouped_inr_value_is_grounded(tmp_path: Path) -> None:
    evidence = {**EVIDENCE, "cost_inr": 535_922.10}
    draft = valid_draft()
    draft["recommended_actions"][0]["rationale"] = "The repair cost is ₹5,35,922.10. [M1]"

    result = run_guardrails(draft, evidence, trace_id="trace-inr", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is True
    assert result.rejections == ()


@pytest.mark.parametrize("text", ["confidence is 90%", "RUL is 4.8 days", "temperature is 180 °F", "cost is 4.8k"])
def test_values_outside_two_percent_band_are_rejected(text: str, tmp_path: Path) -> None:
    result = check(text, tmp_path)

    assert result.accepted is False
    assert any(item.rule == "numeric_grounding" for item in result.rejections)


def test_unknown_numeric_value_is_rejected_with_numeric_rule(tmp_path: Path) -> None:
    result = check("vibration reached 12.7 mm/s", tmp_path)

    assert result.accepted is False
    assert result.rejections[0].rule == "numeric_grounding"
    assert "12.7" in result.rejections[0].offending


@pytest.mark.parametrize("text", ["Replace 23 bearings", "The asset has operated for 2024 hours"])
def test_small_integers_and_four_digit_claims_are_not_blanket_exempt(text: str, tmp_path: Path) -> None:
    result = check(text, tmp_path)

    assert result.accepted is False
    assert any(item.rule == "numeric_grounding" for item in result.rejections)


def test_identifier_citations_and_iso_dates_are_not_numeric_claims(tmp_path: Path) -> None:
    result = check("PUMP-07 record BRG-01 was reviewed on 2026-08-10. [S1]", tmp_path)

    assert result.accepted is True


@pytest.mark.parametrize("hours", [0.5, 1.5, 2.5])
def test_fractional_labour_hours_are_not_ground_checked(hours: float, tmp_path: Path) -> None:
    draft = valid_draft()
    draft["recommended_actions"][0]["est_labour_hours"] = hours

    result = run_guardrails(draft, EVIDENCE, trace_id="trace-labour", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is True
    assert result.rejections == ()


def test_unknown_citation_is_rejected(tmp_path: Path) -> None:
    draft = valid_draft()
    draft["facts"] = ["A missing citation is present. [S9]"]
    result = run_guardrails(draft, EVIDENCE, trace_id="trace-citation", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is False
    assert any(item.rule == "citation_validity" for item in result.rejections)
    assert any(item.offending == "[S9]" for item in result.rejections)


def test_bracketed_evidence_identifier_resolves_to_canonical_citation(tmp_path: Path) -> None:
    evidence = {**EVIDENCE, "maintenance": {"items": [{"id": "[M1]", "cost_gbp": 4200.0}]}}

    result = run_guardrails(valid_draft(), evidence, trace_id="trace-bracketed-id", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is True
    assert not any(item.rule == "citation_validity" for item in result.rejections)


def test_sensor_channel_metadata_preserves_numeric_dimension(tmp_path: Path) -> None:
    evidence = {
        **EVIDENCE,
        "temperature_c": 58.3,
        "sensor": {"items": [{"id": "S1", "channel": "temp_bearing_c", "value": 58.3}, {"id": "S2", "channel": "temp_bearing_c", "value": 78.4}]},
    }
    draft = valid_draft()
    draft["facts"] = ["Bearing temperature is 58.3 °C [S1]"]

    result = run_guardrails(draft, evidence, trace_id="trace-channel-unit", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is True


def test_unsafe_action_is_rejected_and_forces_a0(tmp_path: Path) -> None:
    draft = valid_draft()
    action = dict(draft["recommended_actions"][0])  # type: ignore[index]
    action["action"] = "Bypass the interlock to keep running"
    draft["recommended_actions"] = [action]
    result = run_guardrails(draft, EVIDENCE, trace_id="trace-unsafe", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is False
    assert result.autonomy == "A0"
    assert any(item.rule == "unsafe_action" for item in result.rejections)


def test_uncited_facts_are_moved_to_assumptions_without_being_dropped(tmp_path: Path) -> None:
    draft = valid_draft()
    draft["facts"] = [*draft["facts"], "The operator is available for the intervention."]  # type: ignore[arg-type]
    result = run_guardrails(draft, EVIDENCE, trace_id="trace-assumption", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is True
    assert "The operator is available for the intervention." not in result.draft.facts  # type: ignore[union-attr]
    assert "The operator is available for the intervention." in result.draft.assumptions  # type: ignore[union-attr]
    assert result.moved_facts == ("The operator is available for the intervention.",)


def test_strict_schema_rejects_unknown_fields_and_empty_limits(tmp_path: Path) -> None:
    draft = valid_draft()
    draft["unexpected"] = True
    draft["limits"] = ""
    result = run_guardrails(draft, EVIDENCE, trace_id="trace-schema", database_url=f"sqlite:///{tmp_path / 'plant.db'}")

    assert result.accepted is False
    assert result.draft is None
    assert result.autonomy == "A0"
    assert result.rejections[0].rule == "schema"


def test_valid_draft_is_returned_unchanged(tmp_path: Path) -> None:
    draft = valid_draft()
    result = Guardrail(database_url=f"sqlite:///{tmp_path / 'plant.db'}").check(draft, EVIDENCE, trace_id="trace-valid")

    assert result.accepted is True
    assert result.draft is not None
    assert result.draft.model_dump() == draft
    assert result.autonomy == "A3"


def test_demo_alert_wording_is_grounded(tmp_path: Path) -> None:
    draft = valid_draft()
    draft["fault_diagnosis"] = "bearing degradation predicted, 4.2 days RUL, 87% confidence [S1]"

    result = run_guardrails(
        draft,
        EVIDENCE,
        trace_id="trace-demo-alert",
        database_url=f"sqlite:///{tmp_path / 'plant.db'}",
    )

    assert result.accepted is True
    assert result.draft is not None
    assert result.draft.fault_diagnosis.startswith("bearing degradation predicted")


def test_every_rejection_is_queryable_in_the_audit_ledger(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    result = run_guardrails(valid_draft() | {"unexpected": True}, EVIDENCE, trace_id="trace-persisted", database_url=database_url)
    rows = load_rejections(trace_id="trace-persisted", database_url=database_url)

    assert result.accepted is False
    assert len(rows) >= 1
    assert rows[0].rule == "schema"
    assert rows[0].outcome == "rejected"


def test_guardrail_agent_is_deterministic_and_uses_no_llm(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'plant.db'}"
    result = run_agent(
        GuardrailAgent(database_url=database_url),
        RunContext(trace_id="trace-agent", payload={"draft": valid_draft(), "evidence_bundle": EVIDENCE}),
        database_url=database_url,
        log_path=tmp_path / "app.jsonl",
    )

    assert result.ok is True
    assert result.output["accepted"] is True
    assert result.provider == "deterministic"
