"""Versioned 41-feature contract for cleaned sensor windows.

The contract is intentionally small and deterministic.  It contains the
per-channel, vibration, cross-channel, and completeness features used by the
current ``fv1`` model artifacts.  Context features and the two additional
quality fields described in the broader product specification are reserved
for a future feature-version bump; adding them to ``fv1`` would invalidate the
trained model contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Final, Mapping

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.config import settings
from core.db import session_scope
from core.models import Feature
from core.preprocess import CHANNELS


FEATURE_VERSION: Final[str] = "fv1"
if settings.features.version != FEATURE_VERSION:
    raise RuntimeError(
        f"feature config version mismatch: config={settings.features.version!r}, implementation={FEATURE_VERSION!r}"
    )


class FeatureError(ValueError):
    """Base error for invalid feature inputs or cache records."""


class FeatureVersionMismatch(FeatureError):
    """Raised when an artifact or cache record uses another feature version."""


def _build_feature_names() -> tuple[str, ...]:
    names: list[str] = []
    for channel in CHANNELS:
        names.extend((f"{channel}_mean", f"{channel}_std", f"{channel}_range", f"{channel}_slope"))
    names.extend(
        (
            "vibration_rms_energy",
            "vibration_kurtosis_moment",
            "vibration_crest_factor",
            "vibration_peak_to_peak",
            "vibration_band_energy_ratio",
            "temp_bearing_minus_ambient",
            "current_to_rpm_ratio",
            "vibration_load_residual",
            "window_completeness",
        )
    )
    return tuple(names)


FEATURE_NAMES: Final[tuple[str, ...]] = _build_feature_names()
if len(FEATURE_NAMES) != 41 or len(set(FEATURE_NAMES)) != len(FEATURE_NAMES):
    raise RuntimeError("feature contract must contain 41 unique names")


# Each entry names the physical quantity represented by the corresponding
# vector position.  Keeping this alongside FEATURE_NAMES makes ordering
# reviewable without reverse-engineering the extraction code.
FEATURE_DOCS: Final[dict[str, str]] = {
    **{
        f"{channel}_{stat}": description
        for channel, description in {
            "vibration_rms_mm_s": "vibration RMS velocity in mm/s",
            "vibration_kurtosis": "vibration distribution kurtosis",
            "temp_bearing_c": "bearing temperature in degrees Celsius",
            "temp_motor_c": "motor temperature in degrees Celsius",
            "pressure_bar": "line pressure in bar",
            "current_a": "motor current in amperes",
            "rpm": "rotational speed in revolutions per minute",
            "ambient_temp_c": "ambient temperature in degrees Celsius",
        }.items()
        for stat in ("mean", "std", "range", "slope")
    },
    "vibration_rms_energy": "root-mean-square vibration energy over the window",
    "vibration_kurtosis_moment": "fourth standardized moment of vibration RMS",
    "vibration_crest_factor": "peak vibration divided by RMS vibration",
    "vibration_peak_to_peak": "peak-to-peak vibration amplitude",
    "vibration_band_energy_ratio": "high-frequency vibration energy divided by total non-DC energy",
    "temp_bearing_minus_ambient": "bearing thermal rise above ambient temperature",
    "current_to_rpm_ratio": "motor current per unit rotational speed, a load proxy",
    "vibration_load_residual": "vibration residual not explained by rotational load",
    "window_completeness": "fraction of channel samples that are finite and observed",
}
if set(FEATURE_DOCS) != set(FEATURE_NAMES):
    raise RuntimeError("every feature must have a physical-meaning documentation entry")


def _as_matrix(window: pd.DataFrame) -> np.ndarray:
    if not isinstance(window, pd.DataFrame):
        raise FeatureError("feature window must be a pandas DataFrame")
    missing = [channel for channel in CHANNELS if channel not in window.columns]
    if missing:
        raise FeatureError(f"feature window missing channels: {missing}")
    if window.empty:
        return np.empty((0, len(CHANNELS)), dtype=np.float64)
    values = window.loc[:, list(CHANNELS)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
    values[~np.isfinite(values)] = np.nan
    return values


def _valid(values: np.ndarray) -> np.ndarray:
    return values[np.isfinite(values)]


def _mean(values: np.ndarray) -> float:
    valid = _valid(values)
    return float(valid.mean()) if valid.size else 0.0


def _std(values: np.ndarray) -> float:
    valid = _valid(values)
    return float(valid.std()) if valid.size else 0.0


def _range(values: np.ndarray) -> float:
    valid = _valid(values)
    return float(valid.max() - valid.min()) if valid.size else 0.0


def _slope(values: np.ndarray) -> float:
    valid_mask = np.isfinite(values)
    if int(valid_mask.sum()) < 2:
        return 0.0
    x = np.flatnonzero(valid_mask).astype(np.float64)
    y = values[valid_mask].astype(np.float64)
    x_centered = x - x.mean()
    denominator = float(np.dot(x_centered, x_centered))
    return float(np.dot(x_centered, y - y.mean()) / denominator) if denominator else 0.0


def _per_channel_features(values: np.ndarray) -> list[float]:
    """Describe each physical channel with level, variation, range, and trend."""

    result: list[float] = []
    for channel_index in range(len(CHANNELS)):
        channel = values[:, channel_index]
        result.extend((_mean(channel), _std(channel), _range(channel), _slope(channel)))
    return result


def _vibration_features(values: np.ndarray) -> list[float]:
    """Measure vibration energy, shape, amplitude, and frequency distribution."""

    vibration = values[:, 0]
    valid = _valid(vibration)
    if not valid.size:
        return [0.0] * 5

    mean = float(valid.mean())
    centered = valid - mean
    rms = float(np.sqrt(np.mean(valid**2)))
    variance = float(np.mean(centered**2))
    kurtosis = float(np.mean(centered**4) / variance**2) if variance > 1e-12 else 0.0
    crest_factor = float(np.max(np.abs(valid)) / max(rms, 1e-12))
    peak_to_peak = float(np.ptp(valid))

    # The preprocessor supplies a 60-second grid.  Relative frequency bands
    # keep this feature invariant to the exact window length.
    spectrum = np.abs(np.fft.rfft(centered)) ** 2
    non_dc_energy = float(spectrum[1:].sum()) if spectrum.size > 1 else 0.0
    high_band_start = max(1, spectrum.size // 4)
    band_ratio = float(spectrum[high_band_start:].sum() / non_dc_energy) if non_dc_energy > 1e-12 else 0.0
    return [rms, kurtosis, crest_factor, peak_to_peak, band_ratio]


def _cross_channel_features(values: np.ndarray) -> list[float]:
    """Capture thermal rise, load proxy, and vibration unexplained by load."""

    bearing = values[:, 2]
    ambient = values[:, 7]
    current = values[:, 5]
    rpm = values[:, 6]
    vibration = values[:, 0]

    thermal_rise = bearing - ambient
    ratio = np.divide(current, rpm, out=np.full_like(current, np.nan), where=np.isfinite(rpm) & (rpm > 0))

    valid_mask = np.isfinite(vibration) & np.isfinite(rpm)
    if int(valid_mask.sum()) >= 2 and float(np.ptp(rpm[valid_mask])) > 1e-12:
        design = np.column_stack((np.ones(int(valid_mask.sum())), rpm[valid_mask]))
        coefficients, *_ = np.linalg.lstsq(design, vibration[valid_mask], rcond=None)
        residual = vibration[valid_mask] - design @ coefficients
        load_residual = float(np.sqrt(np.mean(residual**2)))
    else:
        load_residual = 0.0
    return [_mean(thermal_rise), _mean(ratio), load_residual]


def _quality_features(values: np.ndarray) -> list[float]:
    """Report the observed fraction of the cleaned sensor window."""

    return [float(np.isfinite(values).mean()) if values.size else 0.0]


def extract(window: pd.DataFrame) -> np.ndarray:
    """Return the ordered ``fv1`` vector for one cleaned six-hour window.

    The input is expected to have the 60-second grid and quality flags emitted
    by :func:`core.preprocess.preprocess_window`.  Feature extraction does not
    mutate the input and converts malformed numeric cells to missing values.
    The returned array is always shape ``(41,)`` and ``float32``.
    """

    values = _as_matrix(window)
    vector = np.asarray(
        _per_channel_features(values) + _vibration_features(values) + _cross_channel_features(values) + _quality_features(values),
        dtype=np.float32,
    )
    if vector.shape != (41,) or not np.isfinite(vector).all():
        raise FeatureError(f"feature extraction produced an invalid vector: shape={vector.shape}")
    return vector


def assert_feature_version(manifest: Mapping[str, Any]) -> None:
    """Refuse an artifact manifest whose feature contract is not ``fv1``."""

    actual = manifest.get("feature_version")
    if actual != FEATURE_VERSION:
        raise FeatureVersionMismatch(f"feature version mismatch: expected {FEATURE_VERSION!r}, got {actual!r}")


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load and validate a model manifest before feature vectors are served."""

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureError(f"unable to load model manifest: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise FeatureError("model manifest must contain a JSON object")
    assert_feature_version(payload)
    return payload


class FeatureCache:
    """Two-level feature cache keyed by ``(machine_id, window_end, fv)``.

    The in-process dictionary makes repeated reads sub-millisecond.  SQLite
    persistence uses the existing ``features`` table so a process restart can
    reuse vectors.  The current ORM has a uniqueness constraint on
    ``(machine_id, window_end)``; when a future feature version is requested,
    that row is replaced rather than silently returned under the wrong version.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.db_url
        self._memory: dict[tuple[str, int, str], np.ndarray] = {}
        self._lock = RLock()

    @staticmethod
    def _key(machine_id: str, window_end: int, feature_version: str) -> tuple[str, int, str]:
        if not machine_id:
            raise FeatureError("machine_id is required for feature caching")
        return str(machine_id), int(window_end), str(feature_version)

    def get(self, machine_id: str, window_end: int, feature_version: str = FEATURE_VERSION) -> np.ndarray | None:
        """Return a cached vector, or ``None`` when the versioned key misses."""

        key = self._key(machine_id, window_end, feature_version)
        with self._lock:
            cached = self._memory.get(key)
            if cached is not None:
                return cached.copy()

        with session_scope(self.database_url) as session:
            row = session.scalar(
                select(Feature).where(Feature.machine_id == key[0], Feature.window_end == key[1])
            )
            if row is None or row.fv != key[2]:
                return None
            vector = np.asarray(row.vector, dtype=np.float32)
        if vector.shape != (41,):
            raise FeatureError(f"cached vector has invalid shape: {vector.shape}")
        with self._lock:
            self._memory[key] = vector.copy()
        return vector.copy()

    def put(
        self,
        machine_id: str,
        window_end: int,
        vector: np.ndarray,
        feature_version: str = FEATURE_VERSION,
        quality: float | None = None,
    ) -> None:
        """Persist one validated vector under its versioned cache key."""

        key = self._key(machine_id, window_end, feature_version)
        values = np.asarray(vector, dtype=np.float32)
        if values.shape != (41,) or not np.isfinite(values).all():
            raise FeatureError(f"cannot cache invalid feature vector: shape={values.shape}")
        quality_value = float(np.clip(quality if quality is not None else values[-1], 0.0, 1.0))
        with session_scope(self.database_url) as session:
            row = session.scalar(
                select(Feature).where(Feature.machine_id == key[0], Feature.window_end == key[1])
            )
            if row is None:
                session.add(Feature(machine_id=key[0], window_end=key[1], fv=key[2], vector=values.tolist(), quality=quality_value))
            else:
                row.fv = key[2]
                row.vector = values.tolist()
                row.quality = quality_value
        with self._lock:
            self._memory[key] = values.copy()

    def get_or_extract(self, machine_id: str, window_end: int, window: pd.DataFrame, quality: float | None = None) -> np.ndarray:
        """Return a cached vector or extract and persist it on a cache miss."""

        cached = self.get(machine_id, window_end)
        if cached is not None:
            return cached
        vector = extract(window)
        self.put(machine_id, window_end, vector, quality=quality)
        return vector


__all__ = [
    "FEATURE_DOCS",
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "FeatureCache",
    "FeatureError",
    "FeatureVersionMismatch",
    "assert_feature_version",
    "extract",
    "load_manifest",
]
