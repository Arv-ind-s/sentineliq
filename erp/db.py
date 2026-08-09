"""SQLite persistence isolated to the mock ERP process."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from erp.history import ASSETS, generate_work_orders


PARTS = [
    {"part_id": "BRG-6208", "name": "Drive-end bearing", "fault_code": "BRG-01", "in_stock": True, "stock_quantity": 8, "lead_time_days": 2, "unit_cost_gbp": 185.0},
    {"part_id": "SEA-003", "name": "Pump mechanical seal", "fault_code": "SEA-03", "in_stock": True, "stock_quantity": 3, "lead_time_days": 5, "unit_cost_gbp": 420.0},
    {"part_id": "MTR-007", "name": "Motor thermal relay", "fault_code": "MTR-07", "in_stock": False, "stock_quantity": 0, "lead_time_days": 12, "unit_cost_gbp": 275.0},
    {"part_id": "SNS-101", "name": "Pressure transducer", "fault_code": "SNS-01", "in_stock": True, "stock_quantity": 11, "lead_time_days": 1, "unit_cost_gbp": 95.0},
    {"part_id": "LUB-ISO46", "name": "ISO VG 46 lubricant kit", "fault_code": "LUB-05", "in_stock": True, "stock_quantity": 18, "lead_time_days": 1, "unit_cost_gbp": 68.0},
    {"part_id": "PMP-IMP-02", "name": "Pump impeller assembly", "fault_code": "PMP-02", "in_stock": False, "stock_quantity": 0, "lead_time_days": 16, "unit_cost_gbp": 760.0},
]

class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    model TEXT NOT NULL,
                    criticality INTEGER NOT NULL,
                    cost_centre TEXT NOT NULL,
                    line TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_orders (
                    workorder_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                    fault_code TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    scheduled_start TEXT,
                    completed_at TEXT,
                    technician_notes TEXT NOT NULL DEFAULT '',
                    parts TEXT NOT NULL DEFAULT '',
                    labour_hours REAL NOT NULL DEFAULT 0,
                    downtime_minutes INTEGER NOT NULL DEFAULT 0,
                    cost_gbp REAL NOT NULL DEFAULT 0,
                    lead_time_days INTEGER NOT NULL DEFAULT 0,
                    order_type TEXT NOT NULL DEFAULT 'corrective'
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    idempotency_key TEXT PRIMARY KEY,
                    workorder_id TEXT NOT NULL REFERENCES work_orders(workorder_id)
                );
                CREATE INDEX IF NOT EXISTS idx_work_orders_asset_created
                    ON work_orders(asset_id, created_at);
                """,
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(work_orders)").fetchall()}
            if "lead_time_days" not in columns:
                connection.execute("ALTER TABLE work_orders ADD COLUMN lead_time_days INTEGER NOT NULL DEFAULT 0")
            if "order_type" not in columns:
                connection.execute("ALTER TABLE work_orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'corrective'")
            connection.executemany(
                "INSERT OR IGNORE INTO assets(asset_id, name, asset_type, model, criticality, cost_centre, line, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ASSETS,
            )
            existing = connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
            if existing == 0:
                history = generate_work_orders()
                connection.executemany(
                    """INSERT INTO work_orders
                    (workorder_id, asset_id, fault_code, description, priority, status, created_at, updated_at,
                     scheduled_start, completed_at, technician_notes, parts, labour_hours, downtime_minutes,
                     cost_gbp, lead_time_days, order_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            row["workorder_id"], row["asset_id"], row["fault_code"], row["description"],
                            row["priority"], row["status"], row["created_at"], row["updated_at"],
                            row["scheduled_start"], row["completed_at"], row["technician_notes"], row["parts"],
                            row["labour_hours"], row["downtime_minutes"], row["cost_gbp"], row["lead_time_days"], row["order_type"],
                        )
                        for row in history
                    ],
                )

    def count_workorders(self) -> int:
        """Return the number of persisted work orders."""

        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0])

    def replace_with_generated_history(self) -> int:
        """Replace the fixture history with the deterministic 480-row dataset."""

        history = generate_work_orders()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM idempotency_keys")
            connection.execute("DELETE FROM work_orders")
            connection.executemany(
                """INSERT INTO work_orders
                (workorder_id, asset_id, fault_code, description, priority, status, created_at, updated_at,
                 scheduled_start, completed_at, technician_notes, parts, labour_hours, downtime_minutes,
                 cost_gbp, lead_time_days, order_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row["workorder_id"], row["asset_id"], row["fault_code"], row["description"],
                        row["priority"], row["status"], row["created_at"], row["updated_at"],
                        row["scheduled_start"], row["completed_at"], row["technician_notes"], row["parts"],
                        row["labour_hours"], row["downtime_minutes"], row["cost_gbp"], row["lead_time_days"], row["order_type"],
                    )
                    for row in history
                ],
            )
            connection.execute("COMMIT")
        return len(history)

    def healthcheck(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
        return dict(row) if row else None

    def list_workorders(self, asset_id: str | None, since: datetime | None, limit: int) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        if asset_id:
            clauses.append("asset_id = ?")
            values.append(asset_id)
        if since:
            clauses.append("created_at >= ?")
            values.append(since.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM work_orders {where} ORDER BY created_at DESC LIMIT ?", [*values, limit]).fetchall()
        return [dict(row) for row in rows]

    def create_workorder(self, payload: dict[str, Any], idempotency_key: str, now: datetime) -> tuple[dict[str, Any], bool]:
        created_at = now.isoformat()
        workorder_id = f"WO-{uuid4().hex[:12].upper()}"
        parts = ",".join(payload.get("parts", []))
        values = (
            workorder_id,
            payload["asset_id"],
            payload["fault_code"],
            payload["description"],
            payload["priority"],
            "open",
            created_at,
            created_at,
            payload.get("scheduled_start").isoformat() if isinstance(payload.get("scheduled_start"), datetime) else payload.get("scheduled_start"),
            None,
            payload.get("technician_notes", ""),
            parts,
            payload.get("labour_hours", 0),
            payload.get("downtime_minutes", 0),
            payload.get("cost_gbp", 0),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT workorder_id FROM idempotency_keys WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                row = connection.execute("SELECT * FROM work_orders WHERE workorder_id = ?", (existing[0],)).fetchone()
                connection.execute("COMMIT")
                if row is None:
                    raise RuntimeError("Idempotency record points to a missing work order")
                return dict(row), True
            connection.execute(
                """INSERT INTO work_orders
                (workorder_id, asset_id, fault_code, description, priority, status, created_at, updated_at,
                 scheduled_start, completed_at, technician_notes, parts, labour_hours, downtime_minutes, cost_gbp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            connection.execute("INSERT INTO idempotency_keys(idempotency_key, workorder_id) VALUES (?, ?)", (idempotency_key, workorder_id))
            connection.execute("COMMIT")
            row = connection.execute("SELECT * FROM work_orders WHERE workorder_id = ?", (workorder_id,)).fetchone()
        if row is None:
            raise RuntimeError("Created work order could not be read back")
        return dict(row), False

    def update_workorder(self, workorder_id: str, changes: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        fields: dict[str, Any] = {key: value for key, value in changes.items() if value is not None}
        if "scheduled_start" in fields and isinstance(fields["scheduled_start"], datetime):
            fields["scheduled_start"] = fields["scheduled_start"].isoformat()
        if "completed_at" in fields and isinstance(fields["completed_at"], datetime):
            fields["completed_at"] = fields["completed_at"].isoformat()
        if fields.get("status") == "completed" and "completed_at" not in fields:
            fields["completed_at"] = now.isoformat()
        fields["updated_at"] = now.isoformat()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._connect() as connection:
            connection.execute("UPDATE work_orders SET " + assignments + " WHERE workorder_id = ?", [*fields.values(), workorder_id])
            row = connection.execute("SELECT * FROM work_orders WHERE workorder_id = ?", (workorder_id,)).fetchone()
        return dict(row) if row else None

    def list_parts(self, fault_code: str | None) -> list[dict[str, Any]]:
        return [part for part in PARTS if fault_code is None or part["fault_code"] == fault_code]


_databases: dict[str, Database] = {}
_databases_lock = threading.Lock()


def database_for_path(path: Path) -> Database:
    key = str(path)
    with _databases_lock:
        if key not in _databases:
            _databases[key] = Database(path)
        return _databases[key]
