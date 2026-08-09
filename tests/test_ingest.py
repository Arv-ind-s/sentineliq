from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from core.ingest import EventBroker, ReplayManager, StreamClock, process_batch, read_dlq, validate_ingest_point


def _url(tmp_path):
    return f"sqlite:///{tmp_path / 'plant.db'}"


def _point(index: int, vibration: float = 2.0) -> dict[str, object]:
    return {"machine_id": "PUMP-01", "ts": datetime(2025, 2, 26, 0, 0, tzinfo=UTC) + timedelta(minutes=index), "channels": {"vibration_rms_mm_s": vibration, "rpm": 1200.0}}


def test_batch_validation_continues_after_dlq_and_is_idempotent(tmp_path) -> None:
    database_url = _url(tmp_path)
    invalid = _point(1, vibration=1e308)
    result = process_batch([invalid, _point(2)], known_machines=["PUMP-01"], database_url=database_url)
    assert (result.accepted, result.rejected) == (1, 1)
    assert len(read_dlq(database_url)) == 1

    duplicate = process_batch([_point(2)], known_machines=["PUMP-01"], database_url=database_url)
    assert duplicate.accepted == 0
    with sqlite3.connect(tmp_path / "plant.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM ingest_points").fetchone()[0] == 1


def test_degrading_ticks_update_one_stable_alert(tmp_path) -> None:
    database_url = _url(tmp_path)
    points = [_point(index, vibration=8 + index) for index in range(10)]
    result = process_batch(points, known_machines=["PUMP-01"], database_url=database_url)
    assert len(result.alert_ids) == 1
    with sqlite3.connect(tmp_path / "plant.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_replay_source_failure_does_not_stop_other_machines() -> None:
    class GoodSource:
        def __init__(self, machine_id: str) -> None:
            self.machine_id = machine_id

        async def stream(self, clock: StreamClock):
            yield validate_ingest_point({"machine_id": self.machine_id, "ts": 1_740_528_000_000, "channels": {"rpm": 1200.0}})

    class BadSource:
        def __init__(self, machine_id: str) -> None:
            self.machine_id = machine_id

        async def stream(self, clock: StreamClock):
            raise RuntimeError("source unavailable")
            yield  # pragma: no cover

    async def scenario() -> tuple[dict[str, str], list[dict[str, object]]]:
        broker = EventBroker()
        manager = ReplayManager(("PUMP-01", "PUMP-02"), broker, source_factory=lambda machine_id: GoodSource(machine_id) if machine_id == "PUMP-01" else BadSource(machine_id), clock=StreamClock(t0_ts_ms=1_740_528_000_000, speedup=1_000_000))
        await manager.start()
        await asyncio.sleep(0.02)
        await manager.stop()
        return manager.failures, list(broker.history)

    failures, events = asyncio.run(scenario())
    assert "PUMP-02" in failures
    assert any(event["event"] == "telemetry" and event["data"]["machine_id"] == "PUMP-01" for event in events)


def test_stream_clock_rebases_pause_resume_and_speed_changes() -> None:
    wall = [100.0]
    clock = StreamClock(t0_ts_ms=1_000_000, speedup=1.0, monotonic_fn=lambda: wall[0])

    wall[0] += 5
    assert clock.plant_time_ms() == 1_005_000
    clock.set_paused(True)
    wall[0] += 20
    assert clock.plant_time_ms() == 1_005_000

    clock.set_paused(False)
    assert clock.wall_time(1_006_000) == 126.0
    wall[0] += 0.5
    clock.set_speed(2.0)
    assert clock.plant_time_ms() == 1_005_500
    assert clock.wall_time(1_006_500) == 126.0
