from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from core.db import create_schema, get_engine, session_scope
from core.models import Machine, WorkOrder
from erp.history import ASSETS
from erp.history import INJECTION_FIXTURES, MACHINE_IDS, NEAR_DUPLICATE_IDS, T0, generate_work_orders
from scripts.seed_erp_history import seed_erp_history


def test_history_has_exact_size_and_required_non_null_fields() -> None:
    rows = generate_work_orders()

    assert len(rows) == 480
    assert len({row["workorder_id"] for row in rows}) == 480
    assert all(row["fault_code"] and row["cost_gbp"] is not None and row["lead_time_days"] is not None for row in rows)
    assert Counter(row["asset_id"] for row in rows) == Counter({machine_id: 32 for machine_id in MACHINE_IDS})
    assert {row["order_type"] for row in rows} == {"preventive", "corrective", "inspection"}


def test_planted_maintenance_scenarios_are_consistent() -> None:
    rows = generate_work_orders()
    pump = [row for row in rows if row["asset_id"] == "PUMP-07" and row["fault_code"] == "BRG-01"]
    press = [row for row in rows if row["asset_id"] == "PRESS-03" and row["fault_code"] == "SNS-01"]
    cnc_codes = Counter(row["fault_code"] for row in rows if row["asset_id"] == "CNC-11")

    assert any(datetime.fromisoformat(row["created_at"]) <= T0.replace(tzinfo=timezone.utc) for row in pump)
    assert sum(T0 - datetime.fromisoformat(row["created_at"]) <= timedelta(days=90) for row in press) >= 2
    assert cnc_codes.most_common(1)[0][0] == "PMP-02"
    assert cnc_codes.most_common(1)[0][0] != "seal_leak"


def test_injection_fixture_ids_and_near_duplicates_are_explicit() -> None:
    rows = {row["workorder_id"]: row for row in generate_work_orders()}
    fixture_ids = {fixture["workorder_id"] for fixture in INJECTION_FIXTURES}

    assert len(fixture_ids) == 8
    assert fixture_ids.issubset(rows)
    assert all(rows[workorder_id]["technician_notes"] for workorder_id in fixture_ids)
    assert NEAR_DUPLICATE_IDS.issubset(rows)
    for duplicate_id in NEAR_DUPLICATE_IDS:
        machine_id, order_number = duplicate_id[3:].rsplit("-", 1)
        previous_id = f"WO-{machine_id}-{int(order_number) - 1:03d}"
        assert rows[duplicate_id]["asset_id"] == rows[previous_id]["asset_id"]
        assert rows[duplicate_id]["fault_code"] == rows[previous_id]["fault_code"]


def test_seed_command_loads_erp_and_core_mirror(tmp_path: Path) -> None:
    erp_path = tmp_path / "erp.db"
    core_path = tmp_path / "plant.db"
    core_url = f"sqlite:///{core_path}"
    create_schema(core_url)
    with session_scope(core_url) as session:
        session.add_all(
            [
                Machine(
                    id=asset[0], name=asset[1], type=asset[2], model=asset[3], line=asset[6],
                    install_date=0, criticality=asset[4], rated_rpm=1_000,
                    hourly_downtime_cost_gbp=1_800, unit_map={},
                )
                for asset in ASSETS
            ]
        )

    count = seed_erp_history(erp_path, core_url, tmp_path / "fixtures.json")

    assert count == 480
    with sqlite3.connect(erp_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 480
    with session_scope(core_url) as session:
        assert session.query(WorkOrder).count() == 480
    get_engine(core_url).dispose()
