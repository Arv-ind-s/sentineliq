"""Deterministic counterfactual maintenance-policy simulation.

The simulator deliberately has no database, HTTP, or model-loading side
effects.  Callers provide the identical fault-event stream, machine
economics, ERP maintenance costs, and time-indexed model probabilities.  It
then evaluates the three policy definitions from the product specification:

* reactive: every failure is repaired after it happens;
* fixed schedule: two 90-minute preventive windows per 60-day horizon, with
  failures still missed between those windows;
* predictive: one 90-minute planned window for each threshold-crossing event,
  plus the full unplanned MTTR for failures that were missed.

An unmatched threshold-crossing prediction is a false positive and is charged
the same planned window.  Keeping the policy engine pure makes threshold
sensitivity cheap to recompute and prevents the KPI from being coupled to a
particular API or model-serving implementation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil, isfinite
from typing import Any, Iterable, Literal, Mapping


DEFAULT_TAU = 0.42
DEFAULT_HORIZON_DAYS = 60.0
DEFAULT_MAINTENANCE_INTERVAL_DAYS = 30.0
DEFAULT_RECOMMENDED_WINDOW_HOURS = 24.0
MTTR_UNPLANNED_MIN = 320.0
PLANNED_WINDOW_MIN = 90.0
DEFAULT_HOURLY_DOWNTIME_COST_GBP = 1_800.0
DEFAULT_LABOUR_RATE_GBP_PER_HOUR = 110.0

PolicyName = Literal["reactive", "fixed_schedule", "predictive"]
Record = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FaultEvent:
    """A ground-truth failure event in the replay stream."""

    event_id: str | int
    machine_id: str
    onset_ts: int
    failure_ts: int
    actual_downtime_min: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.machine_id).strip():
            raise ValueError("fault event machine_id cannot be empty")
        if self.onset_ts > self.failure_ts:
            raise ValueError(f"fault event {self.event_id!r} has onset after failure")
        if self.actual_downtime_min < 0 or not isfinite(float(self.actual_downtime_min)):
            raise ValueError(f"fault event {self.event_id!r} has invalid actual downtime")


@dataclass(frozen=True, slots=True)
class MachineEconomics:
    """Downtime and maintenance economics for one machine.

    ``corrective_cost_gbp`` and ``preventive_cost_gbp`` are optional direct
    overrides.  When omitted, ``erp_history`` is used to calculate the
    machine's deterministic order-type averages.
    """

    machine_id: str
    hourly_downtime_cost_gbp: float = DEFAULT_HOURLY_DOWNTIME_COST_GBP
    corrective_cost_gbp: float | None = None
    preventive_cost_gbp: float | None = None

    def __post_init__(self) -> None:
        if not self.machine_id.strip():
            raise ValueError("machine economics machine_id cannot be empty")
        for name, value in (
            ("hourly_downtime_cost_gbp", self.hourly_downtime_cost_gbp),
            ("corrective_cost_gbp", self.corrective_cost_gbp),
            ("preventive_cost_gbp", self.preventive_cost_gbp),
        ):
            if value is not None and (value < 0 or not isfinite(float(value))):
                raise ValueError(f"{name} must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class PredictionSignal:
    """A model probability at a timestamp.

    ``event_id`` is preferred because it is the most precise association.  If
    it is absent, the signal is matched to an event on the same machine when
    it falls in the event's recommended pre-failure window.
    """

    signal_id: str | int
    machine_id: str
    ts: int
    probability: float
    event_id: str | int | None = None

    def __post_init__(self) -> None:
        if not self.machine_id.strip():
            raise ValueError("prediction signal machine_id cannot be empty")
        if self.probability < 0 or self.probability > 1 or not isfinite(float(self.probability)):
            raise ValueError("prediction signal probability must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class MachinePolicyResult:
    """Plant-policy outcome for one machine."""

    machine_id: str
    downtime_minutes: float
    downtime_cost_gbp: float
    maintenance_cost_gbp: float
    cost_gbp: float
    planned_actions: int
    false_positive_count: int
    false_positive_cost_gbp: float
    missed_failure_ids: tuple[str, ...]
    missed_failure_costs_gbp: dict[str, float] = field(default_factory=dict)

    @property
    def missed_failures(self) -> tuple[str, ...]:
        """Compatibility-friendly alias used by the UI and reports."""

        return self.missed_failure_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine_id": self.machine_id,
            "downtime_minutes": self.downtime_minutes,
            "downtime_cost_gbp": self.downtime_cost_gbp,
            "maintenance_cost_gbp": self.maintenance_cost_gbp,
            "cost_gbp": self.cost_gbp,
            "planned_actions": self.planned_actions,
            "false_positive_count": self.false_positive_count,
            "false_positive_cost_gbp": self.false_positive_cost_gbp,
            "missed_failure_ids": list(self.missed_failure_ids),
            "missed_failure_costs_gbp": dict(self.missed_failure_costs_gbp),
        }


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Plant-wide result plus the machine-level breakdown."""

    policy: PolicyName
    downtime_minutes: float
    downtime_cost_gbp: float
    maintenance_cost_gbp: float
    cost_gbp: float
    planned_actions: int
    false_positive_count: int
    false_positive_cost_gbp: float
    missed_failure_ids: tuple[str, ...]
    per_machine: dict[str, MachinePolicyResult]

    @property
    def missed_failures(self) -> tuple[str, ...]:
        return self.missed_failure_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "downtime_minutes": self.downtime_minutes,
            "downtime_cost_gbp": self.downtime_cost_gbp,
            "maintenance_cost_gbp": self.maintenance_cost_gbp,
            "cost_gbp": self.cost_gbp,
            "planned_actions": self.planned_actions,
            "false_positive_count": self.false_positive_count,
            "false_positive_cost_gbp": self.false_positive_cost_gbp,
            "missed_failure_ids": list(self.missed_failure_ids),
            "per_machine": {machine_id: result.to_dict() for machine_id, result in self.per_machine.items()},
        }


@dataclass(frozen=True, slots=True)
class DowntimeSimulationResult:
    """Complete deterministic result for one threshold."""

    tau: float
    reduction_pct: float
    cost_reduction_pct: float
    cost_savings_gbp: float
    policies: dict[PolicyName, PolicyResult]

    @property
    def reactive(self) -> PolicyResult:
        return self.policies["reactive"]

    @property
    def fixed_schedule(self) -> PolicyResult:
        return self.policies["fixed_schedule"]

    @property
    def predictive(self) -> PolicyResult:
        return self.policies["predictive"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tau": self.tau,
            "reduction_pct": self.reduction_pct,
            "cost_reduction_pct": self.cost_reduction_pct,
            "cost_savings_gbp": self.cost_savings_gbp,
            "policies": {name: policy.to_dict() for name, policy in self.policies.items()},
        }


def _field(record: Any, *names: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return default
    keys = getattr(record, "keys", None)
    if callable(keys):
        record_keys = set(keys())
        for name in names:
            if name in record_keys:
                return record[name]
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return default


def _timestamp(value: Any, *, field_name: str) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an epoch value or ISO timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an epoch value or ISO timestamp") from exc
    return result


def _number(value: Any, *, field_name: str, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if result < 0 or not isfinite(result):
        raise ValueError(f"{field_name} must be finite and non-negative")
    return result


def _as_records(value: Any, *, default_machine_field: str = "machine_id") -> list[Any]:
    """Flatten either a record iterable or a machine-keyed mapping."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        if any(key in value for key in ("machine_id", "asset_id", "id", "event_id", "signal_id")):
            return [value]
        flattened: list[Any] = []
        for machine_id, records in value.items():
            if isinstance(records, (str, bytes)) or not isinstance(records, Iterable):
                flattened.append({default_machine_field: machine_id, "value": records})
                continue
            for record in records:
                if isinstance(record, Mapping):
                    flattened.append({**record, default_machine_field: record.get(default_machine_field, machine_id)})
                else:
                    flattened.append({default_machine_field: machine_id, "value": record})
        return flattened
    if isinstance(value, (str, bytes)):
        raise TypeError("records must be mappings or iterables of mappings")
    return list(value)


def _normalise_events(events: Iterable[Any] | None) -> list[FaultEvent]:
    result: list[FaultEvent] = []
    for index, raw in enumerate(_as_records(events)):
        if isinstance(raw, FaultEvent):
            result.append(raw)
            continue
        event_id = _field(raw, "event_id", "id", default=index + 1)
        machine_id = str(_field(raw, "machine_id", "asset_id", default="")).strip()
        result.append(
            FaultEvent(
                event_id=event_id,
                machine_id=machine_id,
                onset_ts=_timestamp(_field(raw, "onset_ts", "onset", default=0), field_name="onset_ts"),
                failure_ts=_timestamp(_field(raw, "failure_ts", "failure", default=0), field_name="failure_ts"),
                actual_downtime_min=_number(_field(raw, "actual_downtime_min", default=0), field_name="actual_downtime_min"),
            )
        )
    return result


def _normalise_machines(machine_costs: Any, machine_ids: Iterable[str]) -> dict[str, MachineEconomics]:
    records = _as_records(machine_costs)
    result: dict[str, MachineEconomics] = {}
    for raw in records:
        if isinstance(raw, MachineEconomics):
            machine = raw
        else:
            machine_id = str(_field(raw, "machine_id", "asset_id", "id", default="")).strip()
            value = _field(raw, "value", default=None)
            hourly = _field(raw, "hourly_downtime_cost_gbp", "hourly_cost_gbp", default=value)
            machine = MachineEconomics(
                machine_id=machine_id,
                hourly_downtime_cost_gbp=_number(hourly, field_name="hourly_downtime_cost_gbp", default=DEFAULT_HOURLY_DOWNTIME_COST_GBP),
                corrective_cost_gbp=(None if _field(raw, "corrective_cost_gbp", default=None) is None else _number(_field(raw, "corrective_cost_gbp"), field_name="corrective_cost_gbp")),
                preventive_cost_gbp=(None if _field(raw, "preventive_cost_gbp", default=None) is None else _number(_field(raw, "preventive_cost_gbp"), field_name="preventive_cost_gbp")),
            )
        if machine.machine_id in result and result[machine.machine_id] != machine:
            raise ValueError(f"duplicate economics for machine {machine.machine_id}")
        result[machine.machine_id] = machine
    for machine_id in machine_ids:
        result.setdefault(machine_id, MachineEconomics(machine_id=machine_id))
    return result


def _normalise_predictions(predictions: Any) -> list[PredictionSignal]:
    result: list[PredictionSignal] = []
    for index, raw in enumerate(_as_records(predictions)):
        if isinstance(raw, PredictionSignal):
            result.append(raw)
            continue
        signal_id = _field(raw, "signal_id", "id", default=index + 1)
        result.append(
            PredictionSignal(
                signal_id=signal_id,
                machine_id=str(_field(raw, "machine_id", "asset_id", default="")).strip(),
                ts=_timestamp(_field(raw, "ts", "timestamp", "window_end", default=0), field_name="prediction timestamp"),
                probability=_number(_field(raw, "probability", "p_failure", "p_failure_24h", default=0), field_name="prediction probability"),
                event_id=_field(raw, "event_id", "target_event_id", default=None),
            )
        )
    return result


def _maintenance_cost(raw: Any, labour_rate: float) -> float:
    parts = _field(raw, "parts_cost_gbp", default=None)
    labour = _field(raw, "labour_cost_gbp", default=None)
    if parts is not None or labour is not None:
        return _number(parts, field_name="parts_cost_gbp") + (
            _number(labour, field_name="labour_cost_gbp")
            if labour is not None
            else _number(_field(raw, "labour_hours", default=0), field_name="labour_hours") * labour_rate
        )
    return _number(_field(raw, "cost_gbp", default=0), field_name="cost_gbp")


def _maintenance_averages(history: Any, labour_rate: float) -> dict[str, tuple[float, float]]:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for raw in _as_records(history):
        machine_id = str(_field(raw, "machine_id", "asset_id", default="")).strip()
        if not machine_id:
            continue
        order_type = str(_field(raw, "order_type", "type", default="corrective")).lower()
        grouped[machine_id]["all"].append(_maintenance_cost(raw, labour_rate))
        grouped[machine_id]["preventive" if order_type == "preventive" else "corrective"].append(_maintenance_cost(raw, labour_rate))
    result: dict[str, tuple[float, float]] = {}
    for machine_id, values in grouped.items():
        all_cost = sum(values["all"]) / len(values["all"]) if values["all"] else 0.0
        corrective = sum(values["corrective"]) / len(values["corrective"]) if values["corrective"] else all_cost
        preventive = sum(values["preventive"]) / len(values["preventive"]) if values["preventive"] else all_cost
        result[machine_id] = (corrective, preventive)
    return result


def _policy_result(
    policy: PolicyName,
    per_machine: dict[str, MachinePolicyResult],
) -> PolicyResult:
    missed = tuple(sorted((failure_id for item in per_machine.values() for failure_id in item.missed_failure_ids), key=str))
    return PolicyResult(
        policy=policy,
        downtime_minutes=sum(item.downtime_minutes for item in per_machine.values()),
        downtime_cost_gbp=sum(item.downtime_cost_gbp for item in per_machine.values()),
        maintenance_cost_gbp=sum(item.maintenance_cost_gbp for item in per_machine.values()),
        cost_gbp=sum(item.cost_gbp for item in per_machine.values()),
        planned_actions=sum(item.planned_actions for item in per_machine.values()),
        false_positive_count=sum(item.false_positive_count for item in per_machine.values()),
        false_positive_cost_gbp=sum(item.false_positive_cost_gbp for item in per_machine.values()),
        missed_failure_ids=missed,
        per_machine=per_machine,
    )


def simulate_downtime(
    events: Iterable[Any] | None = None,
    machine_costs: Any = None,
    predictions: Any = None,
    *,
    tau: float = DEFAULT_TAU,
    horizon_days: float = DEFAULT_HORIZON_DAYS,
    maintenance_interval_days: float = DEFAULT_MAINTENANCE_INTERVAL_DAYS,
    recommended_window_hours: float = DEFAULT_RECOMMENDED_WINDOW_HOURS,
    mttr_unplanned_min: float | None = MTTR_UNPLANNED_MIN,
    planned_window_min: float = PLANNED_WINDOW_MIN,
    erp_history: Any = None,
    labour_rate_gbp_per_hour: float = DEFAULT_LABOUR_RATE_GBP_PER_HOUR,
    probability_snapshots: bool = False,
) -> DowntimeSimulationResult:
    """Run all three counterfactual policies for one threshold.

    ``predictions`` contains time-indexed model probabilities.  A signal is a
    true positive when it crosses ``tau`` in the 24-hour (configurable)
    recommended window ending at a fault event; otherwise it is a false
    positive.  At most one planned action is charged per fault event, while
    every unmatched signal is charged as its own false-positive action.

    Passing ``mttr_unplanned_min=None`` uses each event's
    ``actual_downtime_min``.  The default is the specification's fixed
    320-minute unplanned MTTR, which keeps the counterfactual independent of
    the synthetic generator's illustrative event durations.

    When ``probability_snapshots`` is true, consecutive above-threshold rows
    for a machine are treated as one threshold-crossing action.  This is the
    representation used by the persisted ledger history.  The default keeps
    each supplied signal as an independent action for callers that already
    provide normalised intervention signals.
    """

    if tau < 0 or tau > 1 or not isfinite(float(tau)):
        raise ValueError("tau must be in [0, 1]")
    if horizon_days <= 0 or maintenance_interval_days <= 0 or recommended_window_hours < 0:
        raise ValueError("horizon and maintenance interval must be positive; recommended window cannot be negative")
    mttr = None if mttr_unplanned_min is None else _number(mttr_unplanned_min, field_name="mttr_unplanned_min")
    planned_window = _number(planned_window_min, field_name="planned_window_min")
    labour_rate = _number(labour_rate_gbp_per_hour, field_name="labour_rate_gbp_per_hour")

    fault_events = _normalise_events(events)
    signals = _normalise_predictions(predictions)
    machine_ids = {event.machine_id for event in fault_events}
    machine_ids.update(signal.machine_id for signal in signals if signal.machine_id)
    machine_map = _normalise_machines(machine_costs, machine_ids)
    averages = _maintenance_averages(erp_history, labour_rate)
    economics: dict[str, tuple[float, float, float]] = {}
    for machine_id, machine in machine_map.items():
        fallback_corrective, fallback_preventive = averages.get(machine_id, (0.0, 0.0))
        economics[machine_id] = (
            machine.hourly_downtime_cost_gbp,
            machine.corrective_cost_gbp if machine.corrective_cost_gbp is not None else fallback_corrective,
            machine.preventive_cost_gbp if machine.preventive_cost_gbp is not None else fallback_preventive,
        )

    events_by_machine: dict[str, list[FaultEvent]] = defaultdict(list)
    for event in sorted(fault_events, key=lambda item: (item.machine_id, item.failure_ts, str(item.event_id))):
        events_by_machine[event.machine_id].append(event)

    signals_by_machine: dict[str, list[PredictionSignal]] = defaultdict(list)
    previous_above: dict[str, bool] = defaultdict(bool)
    for signal in sorted(signals, key=lambda item: (item.machine_id, item.ts, str(item.signal_id))):
        above = signal.probability >= tau
        if above and (not probability_snapshots or not previous_above[signal.machine_id]):
            signals_by_machine[signal.machine_id].append(signal)
        if probability_snapshots:
            previous_above[signal.machine_id] = above

    matched_event_ids: set[tuple[str, str]] = set()
    matched_signal_ids: set[tuple[str, str]] = set()
    recommended_window_ms = int(recommended_window_hours * 60 * 60 * 1000)
    for machine_id, machine_events in events_by_machine.items():
        for event in machine_events:
            event_key = (machine_id, str(event.event_id))
            for signal in signals_by_machine.get(machine_id, []):
                signal_key = (machine_id, str(signal.signal_id))
                if signal_key in matched_signal_ids:
                    continue
                explicit_match = signal.event_id is not None and str(signal.event_id) == str(event.event_id)
                in_window = event.onset_ts - recommended_window_ms <= signal.ts <= event.failure_ts
                if in_window and (explicit_match or signal.event_id is None):
                    matched_event_ids.add(event_key)
                    matched_signal_ids.add(signal_key)
                    break

    false_positive_signals = [
        signal
        for machine_id, machine_signals in signals_by_machine.items()
        for signal in machine_signals
        if (machine_id, str(signal.signal_id)) not in matched_signal_ids
    ]
    false_positive_keys = {(signal.machine_id, str(signal.signal_id)) for signal in false_positive_signals}
    fixed_actions = int(ceil(horizon_days / maintenance_interval_days))

    reactive_rows: dict[str, MachinePolicyResult] = {}
    fixed_rows: dict[str, MachinePolicyResult] = {}
    predictive_rows: dict[str, MachinePolicyResult] = {}
    for machine_id in sorted(machine_map):
        hourly_cost, corrective_cost, preventive_cost = economics[machine_id]
        machine_events = events_by_machine.get(machine_id, [])
        event_count = len(machine_events)
        unplanned_minutes = sum(mttr if mttr is not None else event.actual_downtime_min for event in machine_events)
        matched_count = sum((machine_id, str(event.event_id)) in matched_event_ids for event in machine_events)
        missed_events = tuple(str(event.event_id) for event in machine_events if (machine_id, str(event.event_id)) not in matched_event_ids)
        false_positive_count = sum(signal.machine_id == machine_id and (signal.machine_id, str(signal.signal_id)) in false_positive_keys for signal in false_positive_signals)

        def row(
            downtime_minutes: float,
            maintenance_cost: float,
            planned_actions: int,
            fp_count: int = 0,
            missed: tuple[str, ...] = (),
            missed_costs: dict[str, float] | None = None,
        ) -> MachinePolicyResult:
            downtime_cost = downtime_minutes * hourly_cost / 60.0
            fp_cost = fp_count * (planned_window * hourly_cost / 60.0 + preventive_cost)
            return MachinePolicyResult(
                machine_id=machine_id,
                downtime_minutes=downtime_minutes,
                downtime_cost_gbp=downtime_cost,
                maintenance_cost_gbp=maintenance_cost,
                cost_gbp=downtime_cost + maintenance_cost,
                planned_actions=planned_actions,
                false_positive_count=fp_count,
                false_positive_cost_gbp=fp_cost,
                missed_failure_ids=missed,
                missed_failure_costs_gbp=missed_costs or {},
            )

        missed_costs = {
            str(event.event_id): (mttr if mttr is not None else event.actual_downtime_min) * hourly_cost / 60.0 + corrective_cost
            for event in machine_events
            if (machine_id, str(event.event_id)) not in matched_event_ids
        }
        reactive_rows[machine_id] = row(unplanned_minutes, event_count * corrective_cost, 0)
        fixed_rows[machine_id] = row(
            fixed_actions * planned_window + unplanned_minutes,
            fixed_actions * preventive_cost + event_count * corrective_cost,
            fixed_actions,
            missed=tuple(str(event.event_id) for event in machine_events),
            missed_costs={
                str(event.event_id): (mttr if mttr is not None else event.actual_downtime_min) * hourly_cost / 60.0 + corrective_cost
                for event in machine_events
            },
        )
        predictive_rows[machine_id] = row(
            (matched_count + false_positive_count) * planned_window + sum(mttr if mttr is not None else event.actual_downtime_min for event in machine_events if (machine_id, str(event.event_id)) not in matched_event_ids),
            (matched_count + false_positive_count) * preventive_cost + len(missed_events) * corrective_cost,
            matched_count + false_positive_count,
            fp_count=false_positive_count,
            missed=missed_events,
            missed_costs=missed_costs,
        )

    policies: dict[PolicyName, PolicyResult] = {
        "reactive": _policy_result("reactive", reactive_rows),
        "fixed_schedule": _policy_result("fixed_schedule", fixed_rows),
        "predictive": _policy_result("predictive", predictive_rows),
    }
    reactive = policies["reactive"]
    predictive = policies["predictive"]
    reduction_pct = ((reactive.downtime_minutes - predictive.downtime_minutes) / reactive.downtime_minutes * 100.0) if reactive.downtime_minutes else 0.0
    cost_savings = reactive.cost_gbp - predictive.cost_gbp
    cost_reduction_pct = (cost_savings / reactive.cost_gbp * 100.0) if reactive.cost_gbp else 0.0
    return DowntimeSimulationResult(
        tau=float(tau),
        reduction_pct=reduction_pct,
        cost_reduction_pct=cost_reduction_pct,
        cost_savings_gbp=cost_savings,
        policies=policies,
    )


def run_downtime_simulation(*args: Any, **kwargs: Any) -> DowntimeSimulationResult:
    """Named wrapper for callers that prefer an explicit operation name."""

    return simulate_downtime(*args, **kwargs)


__all__ = [
    "DEFAULT_TAU",
    "DEFAULT_HORIZON_DAYS",
    "DEFAULT_MAINTENANCE_INTERVAL_DAYS",
    "DEFAULT_RECOMMENDED_WINDOW_HOURS",
    "MTTR_UNPLANNED_MIN",
    "PLANNED_WINDOW_MIN",
    "FaultEvent",
    "MachineEconomics",
    "PredictionSignal",
    "MachinePolicyResult",
    "PolicyResult",
    "DowntimeSimulationResult",
    "simulate_downtime",
    "run_downtime_simulation",
]
