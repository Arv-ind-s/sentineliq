"""Replay, validation, persistence, and live-event primitives for sensor ingest.

The ingest boundary is deliberately independent of FastAPI.  Both the Parquet
replay source and ``POST /ingest/batch`` call the same validation and storage
path, which keeps physical-range rules and idempotency consistent for replay
and external PLC pushes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Protocol

import pyarrow.dataset as ds
import pyarrow.compute as pc

from core.config import ROOT, settings
from core.preprocess import CHANNELS


BASE_TS_MS = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
CHANNEL_RANGES: dict[str, tuple[float, float]] = {
    "vibration_rms_mm_s": (0.0, 200.0),
    "vibration_kurtosis": (0.0, 1_000.0),
    "temp_bearing_c": (-40.0, 300.0),
    "temp_motor_c": (-40.0, 300.0),
    "pressure_bar": (0.0, 100.0),
    "current_a": (0.0, 10_000.0),
    "rpm": (0.0, 100_000.0),
    "ambient_temp_c": (-40.0, 300.0),
}


class IngestValidationError(ValueError):
    """Raised when an external sensor point violates the ingest contract."""


@dataclass(frozen=True)
class ValidatedPoint:
    machine_id: str
    ts: datetime
    ts_ms: int
    channels: dict[str, float]
    is_simulated: bool = False

    def as_raw(self) -> dict[str, Any]:
        return {"machine_id": self.machine_id, "ts": self.ts.isoformat(), "channels": self.channels, "is_simulated": self.is_simulated}


@dataclass(frozen=True)
class BatchResult:
    accepted: int
    rejected: int
    dlq_ids: tuple[str, ...]
    inserted_points: tuple[ValidatedPoint, ...]
    alert_ids: tuple[str, ...]
    alert_records: tuple[AlertRecord, ...] = ()


@dataclass(frozen=True)
class AlertRecord:
    id: str
    machine_id: str
    kind: str
    fault_mode: str
    severity: str
    state: str
    onset_ts_ms: int
    created_ts_ms: int
    updated_ts_ms: int


def database_path(database_url: str | None = None) -> Path:
    """Resolve the SQLite path used by the plant store."""

    url = database_url or settings.db_url
    if not url.startswith("sqlite:///"):
        raise ValueError("ingest persistence currently requires a SQLite DB_URL")
    value = url.removeprefix("sqlite:///")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _connect(database_url: str | None = None) -> sqlite3.Connection:
    path = database_path(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_ingest_tables(database_url: str | None = None) -> None:
    """Create the small runtime tables missing from the generated plant schema."""

    with _connect(database_url) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingest_points (
                machine_id TEXT NOT NULL,
                ts_ms INTEGER NOT NULL,
                channels_json TEXT NOT NULL,
                received_ts_ms INTEGER NOT NULL,
                is_simulated INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (machine_id, ts_ms)
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                machine_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                state TEXT NOT NULL,
                created_ts INTEGER NOT NULL,
                updated_ts INTEGER NOT NULL,
                is_simulated INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ingest_dlq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                machine_id TEXT NOT NULL,
                raw TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            """
        )
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(ingest_points)").fetchall()}
        if "is_simulated" not in columns:
            connection.execute("ALTER TABLE ingest_points ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0")
        alert_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(alerts)").fetchall()}
        if "is_simulated" not in alert_columns:
            connection.execute("ALTER TABLE alerts ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0")


def _timestamp(value: Any) -> tuple[datetime, int]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(float(value) / 1000 if abs(float(value)) > 10_000_000_000 else float(value), tz=UTC)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise IngestValidationError("ts must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    return parsed, int(parsed.timestamp() * 1000)


def _as_mapping(point: Any) -> Mapping[str, Any]:
    if isinstance(point, Mapping):
        return point
    if hasattr(point, "model_dump"):
        value = point.model_dump(mode="json")
        if isinstance(value, Mapping):
            return value
    raise IngestValidationError("point must be an object")


def validate_ingest_point(point: Any, known_machines: Iterable[str] | None = None) -> ValidatedPoint:
    """Validate one point for both replay and external ingest callers."""

    raw = _as_mapping(point)
    machine_id = raw.get("machine_id")
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise IngestValidationError("machine_id is required")
    machine_id = machine_id.strip()
    if known_machines is not None and machine_id not in set(known_machines):
        raise IngestValidationError(f"unknown machine_id: {machine_id}")
    ts, ts_ms = _timestamp(raw.get("ts"))
    is_simulated = raw.get("is_simulated", False)
    if not isinstance(is_simulated, bool):
        raise IngestValidationError("is_simulated must be a boolean")
    channels = raw.get("channels")
    if not isinstance(channels, Mapping) or not channels:
        raise IngestValidationError("channels must contain at least one value")
    clean: dict[str, float] = {}
    for channel, value in channels.items():
        if channel not in CHANNELS:
            raise IngestValidationError(f"unknown channel: {channel}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise IngestValidationError(f"{channel} must be a finite number")
        number = float(value)
        lower, upper = CHANNEL_RANGES[channel]
        if not lower <= number <= upper:
            raise IngestValidationError(f"{channel}={number:g} outside physical range [{lower:g}, {upper:g}]")
        clean[channel] = number
    return ValidatedPoint(machine_id=machine_id, ts=ts, ts_ms=ts_ms, channels=clean, is_simulated=is_simulated)


def _point_json(point: ValidatedPoint) -> str:
    return json.dumps(point.channels, sort_keys=True, separators=(",", ":"))


def _alert_hash(machine_id: str, kind: str, fault_mode: str, onset_bucket: int) -> str:
    payload = json.dumps([machine_id, kind, fault_mode, onset_bucket], separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:48]
    return f"alert-{digest}"


class AlertGenerator:
    """Create stable, content-derived alerts and update them on later ticks."""

    def __init__(self, database_url: str | None = None, bucket_ms: int = 24 * 60 * 60 * 1000) -> None:
        self.database_url = database_url
        self.bucket_ms = bucket_ms
        ensure_ingest_tables(database_url)

    def upsert(
        self,
        *,
        machine_id: str,
        kind: str,
        fault_mode: str,
        severity: str,
        onset_ts_ms: int,
        updated_ts_ms: int | None = None,
        is_simulated: bool = False,
    ) -> AlertRecord:
        now = updated_ts_ms or int(time.time() * 1000)
        bucket = onset_ts_ms // self.bucket_ms
        alert_id = _alert_hash(machine_id, kind, fault_mode, bucket)
        with _connect(self.database_url) as connection:
            row = connection.execute("SELECT created_ts FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            created = int(row["created_ts"]) if row else now
            connection.execute(
                """
                INSERT INTO alerts (id, machine_id, kind, severity, state, created_ts, updated_ts, is_simulated)
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET severity=excluded.severity, updated_ts=excluded.updated_ts, is_simulated=excluded.is_simulated
                """,
                (alert_id, machine_id, kind, severity, created, now, int(is_simulated)),
            )
        return AlertRecord(alert_id, machine_id, kind, fault_mode, severity, "open", onset_ts_ms, created, now)

    def from_point(self, point: ValidatedPoint) -> AlertRecord | None:
        channels = point.channels
        if len(channels) < len(CHANNELS):
            return self.upsert(machine_id=point.machine_id, kind="data", fault_mode="sensor_drift", severity="NODATA", onset_ts_ms=point.ts_ms, updated_ts_ms=point.ts_ms, is_simulated=point.is_simulated)
        vibration = channels.get("vibration_rms_mm_s", 0.0)
        motor_temp = channels.get("temp_motor_c", 0.0)
        pressure = channels.get("pressure_bar", 1.0)
        current = channels.get("current_a", 0.0)
        if channels.get("vibration_kurtosis", 0.0) >= 12.0:
            severity = "CRITICAL" if channels.get("vibration_kurtosis", 0.0) >= 16.0 else "HIGH"
            return self.upsert(machine_id=point.machine_id, kind="machine", fault_mode="rotor_imbalance", severity=severity, onset_ts_ms=point.ts_ms, updated_ts_ms=point.ts_ms, is_simulated=point.is_simulated)
        if vibration >= 8.0:
            severity = "CRITICAL" if vibration >= 16.0 else "HIGH"
            return self.upsert(machine_id=point.machine_id, kind="machine", fault_mode="bearing_wear", severity=severity, onset_ts_ms=point.ts_ms, updated_ts_ms=point.ts_ms, is_simulated=point.is_simulated)
        if motor_temp >= 110.0:
            return self.upsert(machine_id=point.machine_id, kind="machine", fault_mode="motor_overheat", severity="HIGH", onset_ts_ms=point.ts_ms, updated_ts_ms=point.ts_ms, is_simulated=point.is_simulated)
        if pressure <= 1.0:
            return self.upsert(machine_id=point.machine_id, kind="machine", fault_mode="seal_leak", severity="HIGH", onset_ts_ms=point.ts_ms, updated_ts_ms=point.ts_ms, is_simulated=point.is_simulated)
        if current >= 40.0:
            return self.upsert(machine_id=point.machine_id, kind="machine", fault_mode="lubrication_degradation", severity="HIGH", onset_ts_ms=point.ts_ms, updated_ts_ms=point.ts_ms, is_simulated=point.is_simulated)
        return None


def _write_dlq(point: Any, reason: str, database_url: str | None = None) -> str:
    raw = _as_mapping(point) if isinstance(point, Mapping) or hasattr(point, "model_dump") else {"value": repr(point)}
    raw_json = json.loads(json.dumps(raw, default=str))
    timestamp = raw_json.get("ts")
    try:
        _, ts_ms = _timestamp(timestamp)
    except IngestValidationError:
        ts_ms = int(time.time() * 1000)
    machine_id = str(raw_json.get("machine_id", "unknown"))[:32]
    with _connect(database_url) as connection:
        connection.execute("INSERT INTO ingest_dlq (ts, machine_id, raw, reason) VALUES (?, ?, ?, ?)", (ts_ms, machine_id, json.dumps(raw_json, sort_keys=True), reason))
        row_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    return str(row_id)


def process_batch(
    points: Iterable[Any],
    *,
    known_machines: Iterable[str] | None = None,
    database_url: str | None = None,
    alert_generator: AlertGenerator | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> BatchResult:
    """Validate, persist, deduplicate, DLQ, and alert for a batch."""

    ensure_ingest_tables(database_url)
    generator = alert_generator or AlertGenerator(database_url)
    accepted = 0
    rejected = 0
    dlq_ids: list[str] = []
    inserted: list[ValidatedPoint] = []
    alert_ids: list[str] = []
    alert_records: list[AlertRecord] = []
    known = set(known_machines) if known_machines is not None else None
    for raw in points:
        try:
            point = validate_ingest_point(raw, known)
        except (IngestValidationError, TypeError, ValueError) as exc:
            rejected += 1
            dlq_id = _write_dlq(raw, str(exc), database_url)
            dlq_ids.append(dlq_id)
            continue
        with _connect(database_url) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO ingest_points (machine_id, ts_ms, channels_json, received_ts_ms, is_simulated) VALUES (?, ?, ?, ?, ?)",
                (point.machine_id, point.ts_ms, _point_json(point), int(time.time() * 1000), int(point.is_simulated)),
            )
            was_inserted = cursor.rowcount == 1
        if was_inserted:
            accepted += 1
            inserted.append(point)
        alert = generator.from_point(point)
        if alert:
            alert_ids.append(alert.id)
            alert_records.append(alert)
            if on_event:
                on_event("alert", {"alert_id": alert.id, "machine_id": alert.machine_id, "kind": alert.kind, "fault_mode": alert.fault_mode, "severity": alert.severity, "is_simulated": point.is_simulated})
        if on_event:
            on_event("telemetry", {"machine_id": point.machine_id, "ts": point.ts.isoformat(), "channels": point.channels, "is_simulated": point.is_simulated})
    return BatchResult(accepted, rejected, tuple(dlq_ids), tuple(inserted), tuple(dict.fromkeys(alert_ids)), tuple(alert_records))


def read_ingest_points(machine_id: str, start_ms: int, end_ms: int, database_url: str | None = None) -> list[ValidatedPoint]:
    ensure_ingest_tables(database_url)
    with _connect(database_url) as connection:
        rows = connection.execute(
            "SELECT machine_id, ts_ms, channels_json, is_simulated FROM ingest_points WHERE machine_id = ? AND ts_ms BETWEEN ? AND ? ORDER BY ts_ms",
            (machine_id, start_ms, end_ms),
        ).fetchall()
    return [
        ValidatedPoint(machine_id=row["machine_id"], ts=datetime.fromtimestamp(row["ts_ms"] / 1000, tz=UTC), ts_ms=row["ts_ms"], channels=json.loads(row["channels_json"]), is_simulated=bool(row["is_simulated"]))
        for row in rows
    ]


def delete_simulated_points(database_url: str | None = None) -> int:
    """Delete rehearsal rows while leaving external ingest history intact."""

    ensure_ingest_tables(database_url)
    with _connect(database_url) as connection:
        result = connection.execute("DELETE FROM ingest_points WHERE is_simulated = 1")
        return int(result.rowcount)


def delete_alerts(alert_ids: Iterable[str], database_url: str | None = None) -> int:
    """Remove only alerts created by a rehearsal run."""

    identifiers = tuple(dict.fromkeys(str(item) for item in alert_ids if item))
    if not identifiers:
        return 0
    ensure_ingest_tables(database_url)
    placeholders = ",".join("?" for _ in identifiers)
    with _connect(database_url) as connection:
        result = connection.execute(f"DELETE FROM alerts WHERE id IN ({placeholders})", identifiers)
        return int(result.rowcount)


def delete_simulated_alerts(database_url: str | None = None) -> int:
    """Delete rehearsal alerts, including alerts left by a prior process."""

    ensure_ingest_tables(database_url)
    with _connect(database_url) as connection:
        result = connection.execute("DELETE FROM alerts WHERE is_simulated = 1")
        return int(result.rowcount)


def read_parquet_points(machine_id: str, start_ms: int, end_ms: int, sensor_root: str | Path | None = None) -> list[ValidatedPoint]:
    """Read a bounded real sensor window without applying replay delays."""

    root = Path(sensor_root or ROOT / "data" / "sensors")
    if not root.exists():
        return []
    dataset = ds.dataset(root, format="parquet", partitioning="hive")
    columns = set(dataset.schema.names)
    selected_columns = ["machine_id", "ts_utc_ms", *CHANNELS]
    if "is_simulated" in columns:
        selected_columns.append("is_simulated")
    table = dataset.to_table(
        filter=(ds.field("machine_id") == machine_id) & (ds.field("ts_utc_ms") >= start_ms) & (ds.field("ts_utc_ms") <= end_ms),
        columns=selected_columns,
    )
    points: list[ValidatedPoint] = []
    for row in sorted(table.to_pylist(), key=lambda item: item["ts_utc_ms"]):
        channels = {channel: row[channel] for channel in CHANNELS if row.get(channel) is not None}
        try:
            points.append(validate_ingest_point({"machine_id": machine_id, "ts": row["ts_utc_ms"], "channels": channels, "is_simulated": bool(row.get("is_simulated", False))}))
        except IngestValidationError:
            continue
    return points


def latest_complete_timestamp(
    machine_id: str,
    database_url: str | None = None,
    sensor_root: str | Path | None = None,
) -> int | None:
    """Return the newest timestamp containing the complete feature contract."""

    latest: int | None = None
    root = Path(sensor_root or ROOT / "data" / "sensors")
    if root.exists():
        dataset = ds.dataset(root, format="parquet", partitioning="hive")
        table = dataset.to_table(
            filter=ds.field("machine_id") == machine_id,
            columns=["ts_utc_ms", *CHANNELS],
        )
        if table.num_rows:
            mask = None
            for channel in CHANNELS:
                valid = pc.is_valid(table[channel])
                mask = valid if mask is None else pc.and_(mask, valid)
            complete = table.filter(mask) if mask is not None else table
            if complete.num_rows:
                maximum = pc.max(complete["ts_utc_ms"]).as_py()
                latest = int(maximum) if maximum is not None else None

    ensure_ingest_tables(database_url)
    with _connect(database_url) as connection:
        rows = connection.execute(
            "SELECT ts_ms, channels_json FROM ingest_points WHERE machine_id = ? ORDER BY ts_ms DESC",
            (machine_id,),
        ).fetchall()
    for row in rows:
        channels = json.loads(row["channels_json"])
        if all(channel in channels and channels[channel] is not None for channel in CHANNELS):
            timestamp = int(row["ts_ms"])
            latest = timestamp if latest is None else max(latest, timestamp)
            break
    return latest


def read_dlq(database_url: str | None = None) -> list[dict[str, Any]]:
    ensure_ingest_tables(database_url)
    with _connect(database_url) as connection:
        rows = connection.execute("SELECT id, ts, machine_id, raw, reason FROM ingest_dlq ORDER BY id DESC").fetchall()
    return [{"id": str(row["id"]), "ts": datetime.fromtimestamp(row["ts"] / 1000, tz=UTC), "machine_id": row["machine_id"], "raw": json.loads(row["raw"]), "reason": row["reason"]} for row in rows]


class StreamClock:
    """Map plant timestamps onto wall time at the configured replay speed."""

    def __init__(
        self,
        *,
        t0_ts_ms: int | None = None,
        speedup: float | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.t0_ts_ms = t0_ts_ms if t0_ts_ms is not None else BASE_TS_MS + int(settings.stream.replay_start_day * 86_400_000)
        self._monotonic = monotonic_fn
        self._speedup = float(speedup if speedup is not None else settings.stream.speedup)
        self._paused = False
        self._anchor_wall = self._monotonic()
        self._anchor_plant_ms = float(self.t0_ts_ms)
        self._changed = asyncio.Event()

    @property
    def speedup(self) -> float:
        return self._speedup

    @speedup.setter
    def speedup(self, value: float) -> None:
        self.set_speed(value)

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self.set_paused(value)

    def plant_time_ms(self, wall_now: float | None = None) -> float:
        if self._paused:
            return self._anchor_plant_ms
        now = self._monotonic() if wall_now is None else wall_now
        return self._anchor_plant_ms + max(0.0, now - self._anchor_wall) * self._speedup * 1000.0

    def set_speed(self, value: float) -> None:
        speed = float(value)
        if speed <= 0:
            raise ValueError("replay speed must be positive")
        now = self._monotonic()
        current_plant = self.plant_time_ms(now)
        self._anchor_plant_ms = current_plant
        self._anchor_wall = now
        self._speedup = speed
        self._changed.set()

    def set_paused(self, value: bool) -> None:
        paused = bool(value)
        if paused == self._paused:
            return
        now = self._monotonic()
        if paused:
            self._anchor_plant_ms = self.plant_time_ms(now)
            self._anchor_wall = now
            self._paused = True
        else:
            self._anchor_wall = now
            self._paused = False
        self._changed.set()

    def wall_time(self, plant_ts_ms: int) -> float:
        return self._anchor_wall + max(0.0, (plant_ts_ms - self._anchor_plant_ms) / 1000 / self._speedup)

    async def sleep_until(self, plant_ts_ms: int) -> None:
        while True:
            self._changed.clear()
            if self._paused:
                await self._changed.wait()
                continue
            remaining = self.wall_time(plant_ts_ms) - self._monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=min(remaining, 0.25))
            except TimeoutError:
                continue


class SensorSource(Protocol):
    machine_id: str

    async def stream(self, clock: StreamClock) -> AsyncIterator[ValidatedPoint]:
        ...


class ParquetReplaySource:
    """Read one machine's generated Parquet partition as an async source."""

    def __init__(self, machine_id: str, sensor_root: str | Path | None = None, *, start_ts_ms: int | None = None, max_points: int | None = None) -> None:
        self.machine_id = machine_id
        self.sensor_root = Path(sensor_root or ROOT / "data" / "sensors")
        self.start_ts_ms = start_ts_ms
        self.max_points = max_points

    def _rows(self) -> list[ValidatedPoint]:
        if not self.sensor_root.exists():
            return []
        dataset = ds.dataset(self.sensor_root, format="parquet", partitioning="hive")
        start = self.start_ts_ms if self.start_ts_ms is not None else BASE_TS_MS + int(settings.stream.replay_start_day * 86_400_000)
        columns = set(dataset.schema.names)
        selected_columns = ["machine_id", "ts_utc_ms", *CHANNELS]
        if "is_simulated" in columns:
            selected_columns.append("is_simulated")
        table = dataset.to_table(filter=(ds.field("machine_id") == self.machine_id) & (ds.field("ts_utc_ms") >= start), columns=selected_columns)
        rows = sorted(table.to_pylist(), key=lambda row: row["ts_utc_ms"])
        if self.max_points is not None:
            rows = rows[: self.max_points]
        result: list[ValidatedPoint] = []
        for row in rows:
            channels = {channel: row[channel] for channel in CHANNELS if row.get(channel) is not None}
            try:
                result.append(validate_ingest_point({"machine_id": row["machine_id"], "ts": row["ts_utc_ms"], "channels": channels, "is_simulated": bool(row.get("is_simulated", False))}))
            except IngestValidationError:
                continue
        return result

    async def stream(self, clock: StreamClock) -> AsyncIterator[ValidatedPoint]:
        for point in self._rows():
            await clock.sleep_until(point.ts_ms)
            yield point


class EventBroker:
    """Small in-process SSE broker with bounded subscribers and replay."""

    def __init__(self, history_size: int = 2_000, queue_size: int = 256) -> None:
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.queue_size = queue_size
        self._next_id = 0

    def publish(self, event: str, data: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        record = {"id": str(self._next_id), "event": event, "data": data}
        self.history.append(record)
        for queue in tuple(self.subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(record)
        return record

    def subscribe(self, last_event_id: str | None = None) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        try:
            last_id = int(last_event_id or 0)
        except ValueError:
            last_id = 0
        backlog = [record for record in self.history if int(record["id"]) > last_id]
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        self.subscribers.add(queue)
        return queue, backlog

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(queue)

    def clear_history(self) -> None:
        """Drop replayed events while preserving subscriber queues.

        A demo replay reset must not deliver stale alert events to a newly
        opened stream.  Existing subscribers remain connected and will only
        receive events produced after the reset.
        """

        self.history.clear()


class ReplayManager:
    """Run one bounded source per machine; a source failure is isolated."""

    def __init__(self, machine_ids: Iterable[str], broker: EventBroker, source_factory: Callable[[str], SensorSource] | None = None, *, clock: StreamClock | None = None, on_point: Callable[[ValidatedPoint], None] | None = None) -> None:
        self.machine_ids = tuple(machine_ids)
        self.broker = broker
        self.source_factory = source_factory or (lambda machine_id: ParquetReplaySource(machine_id, max_points=2_000))
        self.clock = clock or StreamClock()
        self.on_point = on_point
        self.queues = {machine_id: asyncio.Queue(maxsize=settings.stream.queue_maxsize) for machine_id in self.machine_ids}
        self.failures: dict[str, str] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        for machine_id in self.machine_ids:
            if machine_id not in self.tasks or self.tasks[machine_id].done():
                self.tasks[machine_id] = asyncio.create_task(self._run(machine_id), name=f"replay-{machine_id}")

    async def _run(self, machine_id: str) -> None:
        try:
            source = self.source_factory(machine_id)
            async for point in source.stream(self.clock):
                await self.queues[machine_id].put(point)
                queued = await self.queues[machine_id].get()
                if self.on_point:
                    self.on_point(queued)
                self.broker.publish("telemetry", {"machine_id": queued.machine_id, "ts": queued.ts.isoformat(), "channels": queued.channels})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # source isolation is a core reliability property
            self.failures[machine_id] = str(exc)
            self.broker.publish("run_state", {"machine_id": machine_id, "state": "source_failed", "error": str(exc)})

    async def stop(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
