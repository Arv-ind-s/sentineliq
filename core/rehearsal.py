"""Deterministic fault signatures used by the rehearsal ingest seam.

Rehearsal points deliberately have the same shape as external sensor points.
The caller marks the row as simulated, but downstream validation, persistence,
alerting, and stream publication do not need a rehearsal-specific branch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, Literal

RehearsalMode = Literal[
    "bearing_wear",
    "rotor_imbalance",
    "seal_leak",
    "motor_overheat",
    "lubrication_degradation",
    "sensor_drift",
]
RehearsalSeverity = Literal["low", "medium", "high", "critical"]

MECHANICAL_MODES: Final[frozenset[str]] = frozenset(
    {
        "bearing_wear",
        "rotor_imbalance",
        "seal_leak",
        "motor_overheat",
        "lubrication_degradation",
    }
)

_SEVERITY_GAIN: Final[dict[str, float]] = {
    "low": 0.55,
    "medium": 0.80,
    "high": 1.00,
    "critical": 1.20,
}

_BASE_CHANNELS: Final[dict[str, float]] = {
    "vibration_rms_mm_s": 2.5,
    "vibration_kurtosis": 3.0,
    "temp_bearing_c": 55.0,
    "temp_motor_c": 65.0,
    "pressure_bar": 5.0,
    "current_a": 12.0,
    "rpm": 1_200.0,
    "ambient_temp_c": 22.0,
}


def _normalise_onset(onset: datetime) -> datetime:
    value = onset if onset.tzinfo is not None else onset.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def build_rehearsal_point(machine_id: str, mode: str, severity: str, onset: datetime) -> dict[str, object]:
    """Build one ordinary sensor row carrying a deterministic fault signature."""

    if mode not in MECHANICAL_MODES and mode != "sensor_drift":
        raise ValueError(f"unsupported rehearsal mode: {mode}")
    if severity not in _SEVERITY_GAIN:
        raise ValueError(f"unsupported rehearsal severity: {severity}")

    gain = _SEVERITY_GAIN[severity]
    channels = dict(_BASE_CHANNELS)
    if mode == "bearing_wear":
        channels["vibration_rms_mm_s"] += 5.8 * gain
        channels["vibration_kurtosis"] += 2.0 * gain
        channels["current_a"] += 5.0 * gain
    elif mode == "rotor_imbalance":
        channels["vibration_rms_mm_s"] += 3.0 * gain
        channels["vibration_kurtosis"] += 9.0 * gain
        channels["rpm"] += 170.0 * gain
    elif mode == "seal_leak":
        channels["pressure_bar"] -= 4.2 * gain
        channels["vibration_rms_mm_s"] += 2.0 * gain
    elif mode == "motor_overheat":
        channels["temp_bearing_c"] += 35.0 * gain
        channels["temp_motor_c"] += 50.0 * gain
        channels["current_a"] += 14.0 * gain
    elif mode == "lubrication_degradation":
        channels["temp_bearing_c"] += 18.0 * gain
        channels["current_a"] += 32.0 * gain
        channels["vibration_kurtosis"] += 3.0 * gain
    else:
        # Drift is intentionally plausible but below mechanical alert limits.
        channels["pressure_bar"] += 0.4 * gain
        channels["ambient_temp_c"] += 2.0 * gain

    channels["pressure_bar"] = max(0.1, channels["pressure_bar"])
    channels["rpm"] = max(0.0, channels["rpm"])
    return {
        "machine_id": machine_id,
        "ts": _normalise_onset(onset).isoformat(),
        "channels": channels,
        "is_simulated": True,
    }


def risk_delta(mode: str, severity: str) -> float:
    """Return the visual risk movement represented by one rehearsal tick."""

    if mode == "sensor_drift":
        return 0.0
    return {"low": 8.0, "medium": 18.0, "high": 32.0, "critical": 45.0}[severity]


def failure_horizon(onset: datetime, horizon_hours: int = 24) -> datetime:
    """Define the simulated failure point used for lead-time scoring."""

    return _normalise_onset(onset) + timedelta(hours=horizon_hours)


__all__ = [
    "MECHANICAL_MODES",
    "RehearsalMode",
    "RehearsalSeverity",
    "build_rehearsal_point",
    "failure_horizon",
    "risk_delta",
]
