from __future__ import annotations

import sqlite3
from time import perf_counter
from pathlib import Path

from core.downtime_sim import FaultEvent, MachineEconomics, PredictionSignal, simulate_downtime


def _scenario() -> tuple[list[FaultEvent], list[MachineEconomics], list[PredictionSignal]]:
    events = [
        FaultEvent(1, "PUMP-01", 1_000_000, 1_100_000, actual_downtime_min=35),
        FaultEvent(2, "PUMP-01", 2_000_000, 2_100_000, actual_downtime_min=40),
        FaultEvent(3, "PRESS-01", 1_500_000, 1_600_000, actual_downtime_min=45),
    ]
    machines = [
        MachineEconomics("PUMP-01", hourly_downtime_cost_gbp=1_800),
        MachineEconomics("PRESS-01", hourly_downtime_cost_gbp=2_000),
    ]
    predictions = [
        PredictionSignal("event-1", "PUMP-01", 1_050_000, 0.90, event_id=1),
        PredictionSignal("event-2", "PUMP-01", 2_050_000, 0.85, event_id=2),
        PredictionSignal("event-3", "PRESS-01", 1_550_000, 0.75, event_id=3),
        PredictionSignal("false-1", "PUMP-01", 3_000_000, 0.50),
        PredictionSignal("false-2", "PUMP-01", 4_000_000, 0.30),
    ]
    return events, machines, predictions


def test_returns_machine_and_plant_totals_for_all_three_policies() -> None:
    events, machines, predictions = _scenario()
    result = simulate_downtime(
        events,
        machines,
        predictions,
        erp_history=[
            {"machine_id": "PUMP-01", "order_type": "corrective", "cost_gbp": 200},
            {"machine_id": "PUMP-01", "order_type": "preventive", "cost_gbp": 50},
            {"machine_id": "PRESS-01", "order_type": "corrective", "cost_gbp": 300},
            {"machine_id": "PRESS-01", "order_type": "preventive", "cost_gbp": 75},
        ],
    )

    assert set(result.policies) == {"reactive", "fixed_schedule", "predictive"}
    for policy in result.policies.values():
        assert set(policy.per_machine) == {"PUMP-01", "PRESS-01"}
        assert policy.downtime_minutes == sum(item.downtime_minutes for item in policy.per_machine.values())
        assert policy.cost_gbp == sum(item.cost_gbp for item in policy.per_machine.values())
        assert policy.cost_gbp >= policy.downtime_cost_gbp

    assert result.reactive.downtime_minutes == 3 * 320
    assert result.fixed_schedule.per_machine["PUMP-01"].planned_actions == 2
    assert result.predictive.false_positive_count == 1
    assert result.predictive.per_machine["PUMP-01"].maintenance_cost_gbp == 3 * 50


def test_lower_tau_increases_false_positive_cost_without_changing_event_stream() -> None:
    events, machines, predictions = _scenario()
    low = simulate_downtime(events, machines, predictions, tau=0.20)
    default = simulate_downtime(events, machines, predictions, tau=0.42)
    high = simulate_downtime(events, machines, predictions, tau=0.60)

    assert low.predictive.false_positive_count == 2
    assert default.predictive.false_positive_count == 1
    assert high.predictive.false_positive_count == 0
    assert low.predictive.false_positive_cost_gbp > default.predictive.false_positive_cost_gbp > high.predictive.false_positive_cost_gbp
    assert low.reactive.downtime_minutes == default.reactive.downtime_minutes == high.reactive.downtime_minutes


def test_missed_failures_are_named_and_their_unplanned_cost_is_retained() -> None:
    events, machines, _ = _scenario()
    result = simulate_downtime(events, machines, [], tau=0.42)

    assert result.predictive.missed_failure_ids == ("1", "2", "3")
    assert result.predictive.downtime_minutes == result.reactive.downtime_minutes
    assert result.predictive.cost_gbp == result.reactive.cost_gbp


def test_same_tau_is_bitwise_deterministic_and_recomputation_is_under_80ms() -> None:
    events, machines, predictions = _scenario()
    first = simulate_downtime(events, machines, predictions, tau=0.42)
    started = perf_counter()
    second = simulate_downtime(events, machines, predictions, tau=0.30)
    elapsed = perf_counter() - started
    repeat = simulate_downtime(events, machines, predictions, tau=0.42)

    assert first == repeat
    assert elapsed < 0.08
    assert second.tau == 0.30


def test_actual_downtime_can_be_selected_when_mttr_is_not_configured() -> None:
    event = FaultEvent("actual-1", "CNC-01", 1_000, 2_000, actual_downtime_min=17)
    result = simulate_downtime([event], [MachineEconomics("CNC-01")], [], mttr_unplanned_min=None)

    assert result.reactive.downtime_minutes == 17
    assert result.fixed_schedule.downtime_minutes == 2 * 90 + 17


def test_generated_sixty_event_stream_is_evaluated_identically_for_each_policy() -> None:
    database_path = Path(__file__).resolve().parents[1] / "data" / "plant.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        events = connection.execute("SELECT id AS event_id, machine_id, onset_ts, failure_ts, actual_downtime_min FROM fault_events ORDER BY id").fetchall()
        machines = connection.execute("SELECT id AS machine_id, hourly_downtime_cost_gbp FROM machines ORDER BY id").fetchall()
        history = connection.execute("SELECT machine_id, order_type, cost_gbp FROM work_orders ORDER BY workorder_id").fetchall()
    finally:
        connection.close()

    predictions = [
        {"signal_id": f"event-{row['event_id']}", "machine_id": row["machine_id"], "ts": row["onset_ts"], "probability": 0.9, "event_id": row["event_id"]}
        for row in events
    ]
    result = simulate_downtime(events, machines, predictions, erp_history=history)

    assert len(events) == 60
    assert len(result.reactive.per_machine) == 15
    assert result.reactive.downtime_minutes == 60 * 320
    assert result.predictive.downtime_minutes == 60 * 90
    assert result.predictive.missed_failure_ids == ()
    assert result.reduction_pct == 71.875
