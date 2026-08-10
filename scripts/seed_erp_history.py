"""Seed deterministic ERP history and mirror it into ``core.work_orders``."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import create_schema, session_scope
from core.models import Machine, WorkOrder
from core.runtime_paths import runtime_paths
from erp.db import Database
from erp.history import HISTORY_SIZE, generate_work_orders, injection_fixture_payload


def _epoch_ms(value: str | None) -> int | None:
    return int(datetime.fromisoformat(value).timestamp() * 1000) if value else None


def mirror_core_history(rows: list[dict[str, object]], database_url: str) -> None:
    """Replace the generated mirror while refusing an incomplete machine set."""

    create_schema(database_url)
    with session_scope(database_url) as session:
        machine_ids = set(session.scalars(select(Machine.id)))
        row_ids = {str(row["asset_id"]) for row in rows}
        missing = sorted(row_ids.difference(machine_ids))
        if missing:
            raise RuntimeError(f"cannot mirror ERP history; missing core machines: {missing}")
        session.execute(delete(WorkOrder))
        session.add_all(
            [
                WorkOrder(
                    workorder_id=str(row["workorder_id"]),
                    machine_id=str(row["asset_id"]),
                    fault_code=str(row["fault_code"]),
                    technician_notes=str(row["technician_notes"]),
                    parts_used=[{"part_id": part} for part in str(row["parts"] or "").split(",") if part],
                    labour_hours=float(row["labour_hours"]),
                    downtime_minutes=int(row["downtime_minutes"]),
                    cost_gbp=float(row["cost_gbp"]),
                    lead_time_days=int(row["lead_time_days"]),
                    order_type=str(row["order_type"]),
                    status=str(row["status"]),
                    created_ts=int(_epoch_ms(str(row["created_at"])) or 0),
                    completed_ts=_epoch_ms(str(row["completed_at"])) if row["completed_at"] else None,
                )
                for row in rows
            ]
        )


def seed_erp_history(
    erp_database_path: str | Path | None = None,
    core_database_url: str | None = None,
    fixture_path: str | Path | None = None,
) -> int:
    """Idempotently seed ERP storage, the core mirror, and E-04 metadata."""

    rows = generate_work_orders()
    erp_path = Path(erp_database_path) if erp_database_path is not None else runtime_paths.erp_db
    erp_database = Database(erp_path.resolve())
    erp_database.replace_with_generated_history()
    core_url = core_database_url or f"sqlite:///{runtime_paths.plant_db}"
    mirror_core_history(rows, core_url)
    output_fixture = Path(fixture_path) if fixture_path is not None else runtime_paths.data_root / "erp_injection_fixtures.json"
    output_fixture.parent.mkdir(parents=True, exist_ok=True)
    output_fixture.write_text(json.dumps(injection_fixture_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return erp_database.count_workorders()


def main() -> None:
    count = seed_erp_history()
    print(f"seeded ERP history: {count} work orders; core mirror and injection fixtures updated")


if __name__ == "__main__":
    main()
