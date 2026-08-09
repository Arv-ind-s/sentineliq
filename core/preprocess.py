"""Pure, total preprocessing for a sensor window.

The public entry point intentionally returns a quality report instead of
raising for malformed or unusable telemetry.  Callers can therefore surface a
data-integrity state without turning a bad gateway packet into a service
failure.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Mapping

import numpy as np
import pandas as pd

from core.config import settings


CHANNELS = (
    "vibration_rms_mm_s",
    "vibration_kurtosis",
    "temp_bearing_c",
    "temp_motor_c",
    "pressure_bar",
    "current_a",
    "rpm",
    "ambient_temp_c",
)

CHANNEL_RANGES: dict[str, tuple[float, float]] = {
    "vibration_rms_mm_s": (0.0, 100.0),
    "vibration_kurtosis": (0.0, 100.0),
    "temp_bearing_c": (-40.0, 200.0),
    "temp_motor_c": (-40.0, 200.0),
    "pressure_bar": (0.0, 100.0),
    "current_a": (0.0, 1_000.0),
    "rpm": (0.0, 10_000.0),
    "ambient_temp_c": (-60.0, 100.0),
}


@dataclass(frozen=True)
class QualityReport:
    """Quality vector emitted with every processed window."""

    completeness: float = 0.0
    spikes_removed: int = 0
    interpolated_pct: float = 0.0
    suspect_channels: list[str] = field(default_factory=list)
    quality: str = "INSUFFICIENT"
    missing_pct: float = 1.0
    duplicates_removed: int = 0
    merged_rows: int = 0


def _cfg_value(cfg: Any, name: str, default: Any) -> Any:
    if cfg is None:
        return getattr(settings.preprocess, name, default)
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _source_offsets(cfg: Any) -> Mapping[str, float]:
    offsets = _cfg_value(cfg, "source_offsets_s", {})
    return offsets if isinstance(offsets, Mapping) else {}


def _empty_report(frame: pd.DataFrame, *, duplicates_removed: int = 0, merged_rows: int = 0) -> QualityReport:
    channels = [channel for channel in CHANNELS if channel in frame.columns]
    if not channels or frame.empty:
        completeness = 0.0
    else:
        completeness = float(frame[channels].notna().mean().mean())
    completeness = float(np.clip(completeness, 0.0, 1.0))
    return QualityReport(
        completeness=completeness,
        missing_pct=1.0 - completeness,
        duplicates_removed=duplicates_removed,
        merged_rows=merged_rows,
    )


def _safe_return(frame: Any) -> tuple[pd.DataFrame, QualityReport]:
    """Return a non-raising fallback for invalid input."""

    try:
        result = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    except Exception:
        result = pd.DataFrame()
    for flag in ("interpolated", "missing", "despiked"):
        if flag not in result:
            result[flag] = False
    return result, _empty_report(result)


def _timestamp_column(frame: pd.DataFrame) -> str | None:
    for name in ("ts_utc_ms", "timestamp", "ts", "ts_ms", "time"):
        if name in frame.columns:
            return name
    return None


def _normalise_timestamps(frame: pd.DataFrame, cfg: Any) -> pd.DataFrame:
    """Convert source timestamps to UTC epoch milliseconds and apply skew."""

    timestamp_name = _timestamp_column(frame)
    if timestamp_name is None:
        raise ValueError("missing timestamp column")
    result = frame.copy()
    raw = result[timestamp_name]
    source = result["source"].astype("string").fillna("sensor") if "source" in result else pd.Series("sensor", index=result.index, dtype="string")
    source_offsets = _source_offsets(cfg)

    if pd.api.types.is_numeric_dtype(raw):
        numeric_mask = pd.Series(True, index=raw.index, dtype=bool)
        epoch_ms = pd.to_numeric(raw, errors="coerce").astype("float64")
    else:
        numeric_mask = raw.map(lambda value: isinstance(value, Real) and not isinstance(value, bool))
        if bool(numeric_mask.all()):
            epoch_ms = pd.to_numeric(raw, errors="coerce").astype("float64")
        else:
            numeric_values = pd.to_numeric(raw.where(numeric_mask), errors="coerce").astype("float64")
            parsed = pd.to_datetime(raw.where(~numeric_mask), errors="coerce")
            if getattr(parsed.dt, "tz", None) is None:
                erp_mask = source.str.lower().eq("erp") | source.str.lower().eq("ops")
                erp_datetime_mask = erp_mask & ~numeric_mask
                non_erp_datetime_mask = ~erp_mask & ~numeric_mask
                epoch_ms = numeric_values
                if erp_datetime_mask.any():
                    local = parsed.loc[erp_datetime_mask].dt.tz_localize(
                        str(_cfg_value(cfg, "timezone", settings.plant.timezone)),
                        ambiguous="NaT",
                        nonexistent="shift_forward",
                    )
                    epoch_ms.loc[erp_datetime_mask] = local.dt.tz_convert("UTC").astype("int64") / 1_000_000
                if non_erp_datetime_mask.any():
                    epoch_ms.loc[non_erp_datetime_mask] = parsed.loc[non_erp_datetime_mask].dt.tz_localize("UTC").astype("int64") / 1_000_000
            else:
                epoch_ms = numeric_values
                epoch_ms.loc[~numeric_mask] = parsed.loc[~numeric_mask].dt.tz_convert("UTC").astype("int64") / 1_000_000

    epoch_ms = epoch_ms.to_numpy(dtype="float64")
    if "clock_offset_s" in result.columns:
        offsets = pd.to_numeric(result["clock_offset_s"], errors="coerce").fillna(0).to_numpy(dtype="float64")
        epoch_ms -= offsets * 1_000
    else:
        configured = source.map(lambda value: float(source_offsets.get(str(value), 0.0))).to_numpy(dtype="float64")
        epoch_ms += configured * 1_000
    result["ts_utc_ms"] = np.rint(epoch_ms).astype("int64")
    if timestamp_name != "ts_utc_ms":
        result = result.drop(columns=[timestamp_name])
    return result


def _unit_map_for(frame: pd.DataFrame, cfg: Any, machine_id: Any) -> Mapping[str, str]:
    configured = _cfg_value(cfg, "unit_maps", {})
    if isinstance(configured, Mapping) and configured:
        selected = configured.get(machine_id, configured.get(str(machine_id), configured))
        if isinstance(selected, Mapping):
            return selected
    configured = _cfg_value(cfg, "unit_map", {})
    if isinstance(configured, Mapping) and configured:
        selected = configured.get(machine_id, configured.get(str(machine_id), configured))
        if isinstance(selected, Mapping):
            return selected
    if "unit_map" in frame.columns:
        values = frame.loc[frame.get("machine_id", pd.Series(index=frame.index)).eq(machine_id), "unit_map"]
        for value in values:
            if isinstance(value, Mapping):
                return value
    attrs = frame.attrs.get("unit_maps", {})
    if isinstance(attrs, Mapping) and isinstance(attrs.get(machine_id), Mapping):
        return attrs[machine_id]
    return {}


def _harmonise_units(frame: pd.DataFrame, cfg: Any) -> pd.DataFrame:
    has_inline_units = "unit_map" in frame.columns or bool(frame.attrs.get("unit_maps"))
    has_configured_units = bool(_cfg_value(cfg, "unit_maps", {})) or bool(_cfg_value(cfg, "unit_map", {}))
    if not has_inline_units and not has_configured_units:
        return frame
    result = frame.copy()
    machine_values = result["machine_id"].drop_duplicates().tolist() if "machine_id" in result else [None]
    for machine_id in machine_values:
        mask = result["machine_id"].eq(machine_id) if "machine_id" in result else pd.Series(True, index=result.index)
        units = _unit_map_for(result, cfg, machine_id)
        for channel, unit in units.items():
            if channel not in result.columns:
                continue
            unit_name = str(unit).strip().lower()
            if unit_name in {"f", "fahrenheit", "°f"}:
                result.loc[mask, channel] = (pd.to_numeric(result.loc[mask, channel], errors="coerce") - 32.0) * (5.0 / 9.0)
            elif unit_name in {"psi", "psia", "psig"}:
                result.loc[mask, channel] = pd.to_numeric(result.loc[mask, channel], errors="coerce") * 0.0689475729
    return result


def _clamp_ranges(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    channels = [channel for channel in CHANNELS if channel in result.columns]
    if channels:
        values = result[channels].apply(pd.to_numeric, errors="coerce").to_numpy(dtype="float64")
        lower = np.asarray([CHANNEL_RANGES[channel][0] for channel in channels])
        upper = np.asarray([CHANNEL_RANGES[channel][1] for channel in channels])
        values[~np.isfinite(values) | (values < lower) | (values > upper)] = np.nan
        result[channels] = values
    return result


def _deduplicate(frame: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    sort_columns = [column for column in ("machine_id", "ts_utc_ms") if column in frame.columns]
    already_sorted = "ts_utc_ms" in frame.columns and frame["ts_utc_ms"].is_monotonic_increasing and ("machine_id" not in frame.columns or frame["machine_id"].nunique(dropna=False) <= 1)
    result = frame if not sort_columns or already_sorted else frame.sort_values(sort_columns, kind="mergesort")
    result = result.reset_index(drop=True)
    result["merged_count"] = 1
    exact_keys = [column for column in ("machine_id", "ts_utc_ms", "channel") if column in result.columns]
    if "channel" not in result.columns:
        exact_keys = [column for column in ("machine_id", "ts_utc_ms") if column in result.columns]
    timestamps_unique = already_sorted and result["ts_utc_ms"].is_unique
    duplicates_removed = 0 if timestamps_unique else int(result.duplicated(exact_keys, keep="first").sum()) if exact_keys else 0
    if exact_keys:
        result = result.drop_duplicates(exact_keys, keep="first").reset_index(drop=True)

    merged_rows = 0
    if {"machine_id", "fault_code", "ts_utc_ms"}.issubset(result.columns):
        keep = np.ones(len(result), dtype=bool)
        for _, indices in result.groupby(["machine_id", "fault_code"], sort=False, dropna=False).groups.items():
            ordered = list(indices)
            last_kept: int | None = None
            for index in ordered:
                if last_kept is not None and result.at[index, "ts_utc_ms"] - result.at[last_kept, "ts_utc_ms"] <= 15 * 60 * 1000:
                    keep[index] = False
                    result.at[last_kept, "merged_count"] += int(result.at[index, "merged_count"])
                    merged_rows += 1
                else:
                    last_kept = index
        result = result.loc[keep].reset_index(drop=True)
    return result, duplicates_removed, merged_rows


def _resample(frame: pd.DataFrame, grid_seconds: int) -> pd.DataFrame:
    grid_ms = grid_seconds * 1_000
    result = frame.copy()
    result["_grid_ts"] = (result["ts_utc_ms"] // grid_ms) * grid_ms
    group_keys = ["_grid_ts"]
    if "machine_id" in result.columns:
        group_keys.insert(0, "machine_id")
    aggregations: dict[str, str] = {channel: "mean" for channel in CHANNELS if channel in result.columns}
    for column in ("cycle_id", "is_simulated", "source", "clock_offset_s"):
        if column in result.columns and column not in aggregations:
            aggregations[column] = "first"
    aggregations["merged_count"] = "sum"
    resampled = result.groupby(group_keys, sort=True, dropna=False).agg(aggregations).reset_index()
    resampled = resampled.rename(columns={"_grid_ts": "ts_utc_ms"})
    return resampled


def _expand_grid(frame: pd.DataFrame, grid_seconds: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    grid_ms = grid_seconds * 1_000
    single_machine = "machine_id" not in frame.columns or frame["machine_id"].nunique(dropna=False) <= 1
    timestamps = frame["ts_utc_ms"].to_numpy(dtype=np.int64)
    grid_complete = len(timestamps) <= 1 or bool((np.diff(timestamps) == grid_ms).all())
    if single_machine and grid_complete:
        return frame.reset_index(drop=True)
    groups: list[pd.DataFrame] = []
    groupby = frame.groupby("machine_id", sort=False, dropna=False) if "machine_id" in frame.columns else [(None, frame)]
    for machine_id, group in groupby:
        start = int(group["ts_utc_ms"].min())
        end = int(group["ts_utc_ms"].max())
        grid = np.arange(start, end + grid_ms, grid_ms, dtype=np.int64)
        indexed = group.set_index("ts_utc_ms").reindex(grid)
        indexed.index.name = "ts_utc_ms"
        if "machine_id" in frame.columns:
            indexed["machine_id"] = machine_id
        groups.append(indexed.reset_index())
    result = pd.concat(groups, ignore_index=True, sort=False)
    return result.sort_values([column for column in ("machine_id", "ts_utc_ms") if column in result.columns], kind="mergesort").reset_index(drop=True)


def _hampel(frame: pd.DataFrame, window: int, sigma: float) -> tuple[pd.DataFrame, int]:
    result = frame.copy()
    result["despiked"] = False
    removed = 0
    window = max(1, int(window) | 1)
    channels = [channel for channel in CHANNELS if channel in result.columns]
    groups = list(result.groupby("machine_id", sort=False, dropna=False).groups.values()) if "machine_id" in result.columns else [result.index]
    half = window // 2
    for indices in groups:
        values = result.loc[indices, channels].to_numpy(dtype="float64")
        finite_columns = np.isfinite(values).all(axis=0)
        constant_columns = finite_columns & (np.ptp(values, axis=0) == 0)
        active = np.flatnonzero(~constant_columns)
        if not active.size:
            continue
        active_channels = [channels[int(index)] for index in active]
        values = values[:, active]
        # Filling missing samples only for the robust-statistic calculation
        # keeps the hot path on NumPy's fast median implementation. Missing
        # values remain missing in the output and are handled by gap logic.
        filled = values.copy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            column_medians = np.nanmedian(filled, axis=0)
        column_medians = np.nan_to_num(column_medians, nan=0.0)
        missing_values = ~np.isfinite(filled)
        filled[missing_values] = np.broadcast_to(column_medians, filled.shape)[missing_values]
        padded = np.pad(filled, ((half, half), (0, 0)), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, window, axis=0)
        median = np.median(windows, axis=2)
        mad = np.median(np.abs(windows - median[:, :, None]), axis=2)
        deviation = np.abs(values - median)
        outlier = (((mad > 0) & (deviation > sigma * mad)) | ((mad == 0) & (deviation > 0))) & np.isfinite(values) & np.isfinite(median)
        if outlier.any():
            result.loc[indices, active_channels] = np.where(outlier, median, values)
            row_outliers = outlier.any(axis=1)
            result.loc[np.asarray(list(indices))[row_outliers], "despiked"] = True
            removed += int(outlier.sum())
    return result, removed


def _fill_short_gaps(frame: pd.DataFrame, grid_seconds: int, max_interpolate_minutes: int) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    result = frame.copy()
    interpolated = pd.Series(False, index=result.index)
    channels = [channel for channel in CHANNELS if channel in result.columns]
    if channels and not result[channels].isna().to_numpy(dtype=bool).any():
        return result, interpolated, pd.Series(False, index=result.index)
    max_points = max(0, int(max_interpolate_minutes * 60 // grid_seconds))
    group_indices = list(result.groupby("machine_id", sort=False, dropna=False).groups.values()) if "machine_id" in result.columns else [result.index]
    for channel in CHANNELS:
        if channel not in result.columns:
            continue
        for indices in group_indices:
            values = result.loc[indices, channel].to_numpy(dtype="float64")
            missing = ~np.isfinite(values)
            position = 0
            local_indices = np.asarray(list(indices))
            while position < len(values):
                if not missing[position]:
                    position += 1
                    continue
                start = position
                while position < len(values) and missing[position]:
                    position += 1
                end = position
                if start > 0 and end < len(values) and end - start <= max_points and np.isfinite(values[start - 1]) and np.isfinite(values[end]):
                    values[start:end] = np.linspace(values[start - 1], values[end], end - start + 2)[1:-1]
                    interpolated.loc[local_indices[start:end]] = True
            result.loc[indices, channel] = values
    missing = result[list(CHANNELS)].isna().any(axis=1)
    return result, interpolated, missing


def _frozen_channels(frame: pd.DataFrame, grid_seconds: int, frozen_minutes: int) -> list[str]:
    minimum_points = max(2, int(math.ceil(frozen_minutes * 60 / grid_seconds)))
    suspects: list[str] = []
    channels = [channel for channel in CHANNELS if channel in frame.columns]
    groups = list(frame.groupby("machine_id", sort=False, dropna=False).groups.values()) if "machine_id" in frame.columns else [frame.index]
    for indices in groups:
        values = frame.loc[indices, channels].to_numpy(dtype="float64")
        if len(values) < minimum_points:
            continue
        finite_columns = np.isfinite(values).all(axis=0)
        globally_frozen = finite_columns & (np.std(values, axis=0) <= 1e-9)
        for channel, is_frozen in zip(channels, globally_frozen):
            if bool(is_frozen) and channel not in suspects:
                suspects.append(channel)

        remaining = np.flatnonzero(~globally_frozen)
        if remaining.size:
            windows = np.lib.stride_tricks.sliding_window_view(values[:, remaining], minimum_points, axis=0)
            complete = np.isfinite(windows).all(axis=2)
            frozen = (complete & (np.std(windows, axis=2) <= 1e-9)).any(axis=0)
            for channel_index, is_frozen in zip(remaining, frozen):
                channel = channels[int(channel_index)]
                if bool(is_frozen) and channel not in suspects:
                    suspects.append(channel)
    return suspects


def preprocess_window(df: pd.DataFrame, cfg: Any = None) -> tuple[pd.DataFrame, QualityReport]:
    """Clean and align a sensor window, returning data and its quality vector.

    The function is total: invalid timestamps, missing columns, non-finite
    values, and all-NaN windows return a report rather than propagating an
    exception to the ingest or prediction path.
    """

    try:
        if not isinstance(df, pd.DataFrame):
            return _safe_return(df)
        if df.empty:
            result = df.copy()
            for channel in CHANNELS:
                if channel not in result:
                    result[channel] = pd.Series(dtype="float64")
            for flag in ("interpolated", "missing", "despiked"):
                result[flag] = pd.Series(dtype="bool")
            return result, _empty_report(result)

        normalised = _normalise_timestamps(df, cfg)
        normalised = _harmonise_units(normalised, cfg)
        normalised = _clamp_ranges(normalised)
        normalised, duplicates_removed, merged_rows = _deduplicate(normalised)
        grid_seconds = int(_cfg_value(cfg, "grid_seconds", settings.preprocess.grid_seconds))
        result = _expand_grid(_resample(normalised, grid_seconds), grid_seconds)
        for channel in CHANNELS:
            if channel not in result:
                result[channel] = np.nan
        result, spikes_removed = _hampel(
            result,
            int(_cfg_value(cfg, "hampel_window", settings.preprocess.hampel_window)),
            float(_cfg_value(cfg, "hampel_sigma", settings.preprocess.hampel_sigma)),
        )
        result, interpolated, missing = _fill_short_gaps(
            result,
            grid_seconds,
            int(_cfg_value(cfg, "max_interpolate_minutes", settings.preprocess.max_interpolate_minutes)),
        )
        result["interpolated"] = interpolated.to_numpy(dtype=bool)
        result["missing"] = missing.to_numpy(dtype=bool)
        result["despiked"] = result["despiked"].fillna(False).astype(bool)
        result["ts_utc_ms"] = result["ts_utc_ms"].astype("int64")

        suspects = _frozen_channels(result, grid_seconds, int(_cfg_value(cfg, "frozen_sensor_minutes", settings.preprocess.frozen_sensor_minutes)))
        observed = result[list(CHANNELS)].notna()
        completeness = float(observed.to_numpy(dtype=bool).mean()) if observed.size else 0.0
        completeness = float(np.clip(completeness, 0.0, 1.0))
        quality = "INSUFFICIENT" if completeness < float(_cfg_value(cfg, "min_completeness", settings.preprocess.min_completeness)) else "SUSPECT" if suspects else "GOOD"
        report = QualityReport(
            completeness=completeness,
            spikes_removed=spikes_removed,
            interpolated_pct=float(interpolated.mean()) if len(interpolated) else 0.0,
            suspect_channels=suspects,
            quality=quality,
            missing_pct=1.0 - completeness,
            duplicates_removed=duplicates_removed,
            merged_rows=merged_rows,
        )
        return result, report
    except Exception:
        return _safe_return(df)


__all__ = ["QualityReport", "preprocess_window"]
