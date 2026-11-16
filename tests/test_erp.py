from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from erp.app import app, fault_injector


ROOT = Path(__file__).parents[1]


@pytest.fixture
def erp_client(monkeypatch: pytest.MonkeyPatch):
    test_dir = ROOT / ".test-artifacts" / f"erp-{uuid4().hex}"
    test_dir.mkdir(parents=True)
    db_path = test_dir / "erp.db"
    monkeypatch.setenv("ERP_API_KEY", "test-erp-key")
    monkeypatch.setenv("ERP_READ_API_KEY", "independent-read-key")
    monkeypatch.setenv("ERP_DB_PATH", str(db_path))
    monkeypatch.setenv("ERP_FAULT_MODE", "none")
    fault_injector._counter = 0
    try:
        yield TestClient(app)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def auth(scope_header: str | None = None) -> dict[str, str]:
    headers = {"Authorization": "Bearer test-erp-key"}
    if scope_header is not None:
        headers["X-ERP-Scopes"] = scope_header
    return headers


def test_health_is_available_and_reports_the_separate_service(erp_client: TestClient) -> None:
    response = erp_client.get("/erp/v1/health")

    assert response.status_code == 200
    assert response.json()["service"] == "mock-erp"
    assert response.json()["database"] == "up"


def test_bearer_auth_and_scopes(erp_client: TestClient) -> None:
    assert erp_client.get("/erp/v1/assets/PUMP-07").status_code == 401
    assert erp_client.get("/erp/v1/assets/PUMP-07", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert erp_client.get("/erp/v1/assets/PUMP-07", headers=auth("workorder:write")).status_code == 403
    assert erp_client.get("/erp/v1/assets/PUMP-07", headers=auth("workorder:read")).status_code == 200


def test_scope_header_cannot_elevate_a_read_only_token(erp_client: TestClient) -> None:
    response = erp_client.post("/erp/v1/workorders", headers={
        "Authorization": "Bearer independent-read-key",
        "X-ERP-Scopes": "workorder:write", "Idempotency-Key": "escalation",
    }, json={"asset_id": "PUMP-07", "fault_code": "BRG-01", "description": "Unauthorized repair"})
    assert response.status_code == 403
    assert erp_client.get("/erp/v1/assets/PUMP-07", headers={"Authorization": "Bearer independent-read-key"}).status_code == 200
    assert erp_client.get("/erp/v1/assets/PUMP-07", headers={"Authorization": "Bearer independent-read-key|workorder:write"}).status_code == 401


def test_all_six_erp_endpoints_have_fixture_behaviour(erp_client: TestClient) -> None:
    headers = auth()
    assert erp_client.get("/erp/v1/assets/PUMP-07", headers=headers).json()["asset_id"] == "PUMP-07"
    assert erp_client.get("/erp/v1/workorders?asset_id=PUMP-07", headers=headers).status_code == 200
    assert erp_client.get("/erp/v1/parts?fault_code=BRG-01", headers=headers).json()[0]["part_id"] == "BRG-6208"
    assert erp_client.patch("/erp/v1/workorders/WO-PUMP-07-001", headers=headers, json={"status": "in_progress"}).status_code == 200
    assert erp_client.post("/erp/v1/workorders", headers={**headers, "Idempotency-Key": "fixture-create-1"}, json={"asset_id": "PUMP-07", "fault_code": "BRG-01", "description": "Inspect bearing"}).status_code == 201


def test_parts_catalog_covers_every_seeded_fault_code(erp_client: TestClient) -> None:
    response = erp_client.get("/erp/v1/parts", headers=auth("workorder:read"))

    assert response.status_code == 200
    assert {part["fault_code"] for part in response.json()} == {"BRG-01", "SEA-03", "MTR-07", "SNS-01", "LUB-05", "PMP-02"}


def test_idempotency_creates_one_row_and_replays_the_same_id(erp_client: TestClient) -> None:
    headers = {**auth(), "Idempotency-Key": "same-request"}
    payload = {"asset_id": "PUMP-07", "fault_code": "BRG-01", "description": "Replace bearing", "priority": "high"}

    first = erp_client.post("/erp/v1/workorders", headers=headers, json=payload)
    second = erp_client.post("/erp/v1/workorders", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["workorder_id"] == second.json()["workorder_id"]
    with sqlite3.connect(os.environ["ERP_DB_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_orders WHERE description = 'Replace bearing'").fetchone()[0] == 1


def test_flaky_mode_is_exactly_thirty_percent_over_fifty_requests(erp_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_FAULT_MODE", "flaky")
    statuses = [erp_client.get("/erp/v1/assets/PUMP-07", headers=auth("workorder:read")).status_code for _ in range(50)]

    assert statuses.count(503) == 15
    assert statuses.count(200) == 35


def test_down_mode_does_not_start_a_listener() -> None:
    test_dir = ROOT / ".test-artifacts" / f"erp-down-{uuid4().hex}"
    test_dir.mkdir(parents=True)
    environment = os.environ.copy()
    environment["ERP_FAULT_MODE"] = "down"
    environment["ERP_DB_PATH"] = str(test_dir / "erp.db")
    try:
        process = subprocess.run([sys.executable, "scripts/run_erp.py"], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    assert process.returncode == 1
    assert "no listener was started" in process.stderr
