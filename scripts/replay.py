"""Re-run a persisted agent trace and print a field-level output diff."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.base import AgentResult, RunContext, load_agent_runs
from agents.coordinator_agent import CoordinatorAgent
from agents.guardrail_agent import GuardrailAgent
from agents.maintenance_log_agent import MaintenanceLogAgent
from agents.sensor_agent import SensorAgent
from core.config import settings
from core.db import get_engine
from core.predict import load_predictor
from core.runtime_paths import runtime_paths


class ReplayError(RuntimeError):
    """Raised when a stored trace cannot be reproduced safely."""


@dataclass(frozen=True)
class FieldDiff:
    agent: str
    path: str
    stored: Any
    current: Any


def _epoch_ms(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return int(round(number if abs(number) >= 1e11 else number * 1_000))
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return int(timestamp.tz_convert("UTC").timestamp() * 1_000)


class _ParquetTSDBClient:
    """Small replay-only TSDB adapter for the current Parquet data layout."""

    def __init__(self, sensor_root: Path = runtime_paths.sensor_root) -> None:
        if not sensor_root.exists():
            raise ReplayError(f"sensor data is missing: {sensor_root}")
        self.dataset = ds.dataset(sensor_root, format="parquet", partitioning="hive")
        names = set(self.dataset.schema.names)
        self.timestamp_column = "canonical_ts_ms" if "canonical_ts_ms" in names else "ts_utc_ms" if "ts_utc_ms" in names else ""
        if not self.timestamp_column:
            raise ReplayError("sensor data has no canonical_ts_ms or ts_utc_ms column")

    def read_window(self, machine_id: str, t0: Any, t1: Any, channels: Sequence[str]) -> pd.DataFrame:
        selected = [channel for channel in channels if channel in self.dataset.schema.names]
        columns = [self.timestamp_column, *selected]
        if "machine_id" in self.dataset.schema.names:
            columns.insert(0, "machine_id")
        expression = ds.field("machine_id") == machine_id if "machine_id" in self.dataset.schema.names else None
        timestamp_filter = (ds.field(self.timestamp_column) >= _epoch_ms(t0)) & (ds.field(self.timestamp_column) <= _epoch_ms(t1))
        expression = timestamp_filter if expression is None else expression & timestamp_filter
        table = self.dataset.to_table(columns=list(dict.fromkeys(columns)), filter=expression)
        frame = table.to_pandas()
        if self.timestamp_column != "ts_utc_ms" and self.timestamp_column in frame:
            frame = frame.rename(columns={self.timestamp_column: "ts_utc_ms"})
        return frame.sort_values("ts_utc_ms", kind="mergesort").reset_index(drop=True) if not frame.empty else frame


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(child) for child in value]
    if isinstance(value, np.ndarray):
        return _json_ready(value.tolist())
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump(mode="json"))
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _diff(stored: Any, current: Any, *, agent: str, path: str = "$") -> list[FieldDiff]:
    if isinstance(stored, Mapping) and isinstance(current, Mapping):
        differences: list[FieldDiff] = []
        for key in sorted(set(stored) | set(current), key=str):
            child_path = f"{path}.{key}"
            if key not in stored:
                differences.append(FieldDiff(agent, child_path, "<missing>", _json_ready(current[key])))
            elif key not in current:
                differences.append(FieldDiff(agent, child_path, _json_ready(stored[key]), "<missing>"))
            else:
                differences.extend(_diff(stored[key], current[key], agent=agent, path=child_path))
        return differences
    if isinstance(stored, Sequence) and not isinstance(stored, (str, bytes)) and isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
        differences = []
        for index in range(max(len(stored), len(current))):
            child_path = f"{path}[{index}]"
            if index >= len(stored):
                differences.append(FieldDiff(agent, child_path, "<missing>", _json_ready(current[index])))
            elif index >= len(current):
                differences.append(FieldDiff(agent, child_path, _json_ready(stored[index]), "<missing>"))
            else:
                differences.extend(_diff(stored[index], current[index], agent=agent, path=child_path))
        return differences
    stored_ready = _json_ready(stored)
    current_ready = _json_ready(current)
    return [] if stored_ready == current_ready else [FieldDiff(agent, path, stored_ready, current_ready)]


def _agent(name: str, database_url: str | None) -> Any:
    if name == "sensor":
        return SensorAgent(_ParquetTSDBClient(), load_predictor(), database_url=database_url)
    if name == "maintenance_log":
        return MaintenanceLogAgent(database_url=database_url)
    if name == "coordinator":
        return CoordinatorAgent()
    if name == "guardrail":
        return GuardrailAgent(database_url=database_url)
    raise ReplayError(f"no replay factory exists for persisted agent '{name}'")


def _sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///") or ":memory:" in database_url:
        return None
    value = database_url.removeprefix("sqlite:///")
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


@contextmanager
def _isolated_database(database_url: str) -> Iterator[str]:
    source_path = _sqlite_path(database_url)
    if source_path is None:
        yield database_url
        return
    with tempfile.TemporaryDirectory(prefix="sentinel-replay-") as directory:
        destination = Path(directory) / "plant.db"
        if source_path.exists():
            with closing(sqlite3.connect(source_path, timeout=5)) as source, closing(sqlite3.connect(destination, timeout=5)) as target:
                source.backup(target)
        yield f"sqlite:///{destination.as_posix()}"


def replay_trace(trace_id: str, *, database_url: str | None = None) -> list[FieldDiff]:
    """Replay every persisted agent in ``trace_id`` against current code."""

    if not trace_id.strip():
        raise ValueError("trace_id must not be empty")
    database_url = database_url or settings.db_url
    stored_runs = load_agent_runs(trace_id, database_url=database_url)
    if not stored_runs:
        raise ReplayError(f"no persisted agent runs found for trace_id={trace_id}")
    missing_input = [run.agent for run in stored_runs if "_run_input" not in run.output]
    if missing_input:
        agents = ", ".join(sorted(set(missing_input)))
        raise ReplayError(f"trace_id={trace_id} predates replay inputs for agent(s): {agents}; run a fresh trace first")

    differences: list[FieldDiff] = []
    with _isolated_database(database_url) as replay_database_url:
        try:
            for run in stored_runs:
                payload = run.output["_run_input"]
                if not isinstance(payload, Mapping):
                    raise ReplayError(f"stored replay input for agent={run.agent} is not an object")
                current_agent = _agent(run.agent, replay_database_url)
                current_result: AgentResult = current_agent.run(RunContext(trace_id=trace_id, payload=payload))
                stored_output = {key: value for key, value in run.output.items() if key not in {"_run_input", "_agent_result"}}
                differences.extend(_diff(stored_output, current_result.output, agent=run.agent))
                close = getattr(current_agent, "close", None)
                if callable(close):
                    close()
        finally:
            isolated_engine = get_engine(replay_database_url)
            isolated_engine.dispose()
    return differences


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", required=True, help="Persisted diagnosis trace identifier")
    parser.add_argument("--db-url", default=None, help="SQLAlchemy plant DB URL; defaults to DB_URL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        differences = replay_trace(args.trace_id, database_url=args.db_url)
    except (OSError, ReplayError, ValueError) as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 1
    print(f"REPLAY trace_id={args.trace_id} differences={len(differences)}")
    if not differences:
        print("NO DIFF: all persisted fields match current code output.")
        return 0
    for difference in differences:
        stored = json.dumps(difference.stored, sort_keys=True, default=str)
        current = json.dumps(difference.current, sort_keys=True, default=str)
        print(f"DIFF agent={difference.agent} field={difference.path} stored={stored} current={current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
