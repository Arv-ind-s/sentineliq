from __future__ import annotations

import pytest

from eval.harness import EvaluationConfig, _adversarial_metrics, _injection_metrics, load_scenarios, render_report


def test_scenario_catalog_contains_exactly_twelve_unique_adversarial_bundles() -> None:
    scenarios = load_scenarios()

    assert len(scenarios) == 12
    assert len({scenario["id"] for scenario in scenarios}) == 12
    assert {scenario["kind"] for scenario in scenarios} == {"empty_evidence", "contradictory_evidence", "injected_evidence"}


def test_adversarial_harness_reports_hedged_outputs_without_fabrication() -> None:
    result = _adversarial_metrics(load_scenarios())

    assert result["passed"] == 12
    assert result["fabrications"] == 0
    assert result["citation_coverage"] >= 0.95
    assert result["numeric_grounding_first_try"] >= 0.95


def test_injection_fixture_metrics_keep_known_evasions_visible() -> None:
    result = _injection_metrics()

    assert result["total"] == 8
    assert result["matched"] == 5
    assert result["known_evasions"] == 3


def test_report_contains_tables_confusion_matrix_and_run_provenance() -> None:
    summary = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "model": "test-model",
        "run_mode": "templated",
        "gates": {
            "failure_balanced_accuracy": {"observed": 1.0, "target": 0.85, "passed": True},
            "downtime_reduction": {"observed": 0.4, "target": 20.0, "passed": True},
        },
        "prediction": {
            "per_event_detection": {"detected": 1, "total": 1, "rate": 1.0},
            "failure": {"balanced_accuracy": 1.0, "precision": 1.0, "recall": 1.0, "f1": 1.0},
            "fault_mode": {"labels": ["bearing_wear"], "top1_accuracy": 1.0, "top2_accuracy": 1.0, "confusion_matrix": [[1]]},
            "rul": {"mae_days": 1.0, "p10_p90_coverage": 0.8, "raw_p10_p90_coverage": 0.7, "interval_calibration": {"lower_adjustment_days": 1.0, "upper_adjustment_days": 0.0}},
        },
        "hallucination": {"passed": 1, "total": 1, "fabrications": 0, "scenarios": []},
        "injection": {"matched": 1, "total": 1, "known_evasions": 0, "blind_total": 0},
        "reliability": {"completed": 1, "runs": 1, "unhandled": 0, "erp_fault_mode": "flaky", "llm_enabled": False},
        "latency": {"run_mode": "templated", "model": "test-model", "stages": {}, "tokens_per_diagnosis": {"p50": 0, "p95": 0, "max": 0}},
    }
    report = render_report(summary)

    assert "## Gates" in report
    assert "failure balanced accuracy" in report
    assert "Measured downtime reduction" in report
    assert "reduction ≥ 0.20 ⟺ recall ≥ 0.278 + 0.39·(FP/N)" in report
    assert "### Fault-mode confusion matrix" in report
    assert "Measured mode:" in report
    assert "Tokens/diagnosis" in report


def test_reliability_run_count_must_be_positive() -> None:
    with pytest.raises(ValueError, match="reliability_runs"):
        EvaluationConfig(reliability_runs=0)
