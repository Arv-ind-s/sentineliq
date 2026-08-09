from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from api.schemas import LedgerResponse


ROOT = Path(__file__).resolve().parents[1]
SCREEN_SOURCE = (ROOT / "ui" / "src" / "screens" / "screens.tsx").read_text(encoding="utf-8")
TYPE_SOURCE = (ROOT / "ui" / "src" / "types" / "api.ts").read_text(encoding="utf-8")


def test_ledger_screen_implements_the_demo_interactions() -> None:
    for marker in (
        "keepPreviousData",
        "requestAnimationFrame",
        "motion.section",
        "ledger-target-line",
        "What the model missed",
        "aria-expanded={showMissed}",
        "setDebouncedTau",
    ):
        assert marker in SCREEN_SOURCE
    assert "LedgerMissedFailure" in TYPE_SOURCE


def test_ledger_contract_exposes_missed_failures_with_charged_cost() -> None:
    client = TestClient(app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    response = client.get("/ledger")
    assert response.status_code == 200
    payload = LedgerResponse.model_validate(response.json())

    assert payload.missed_failures
    assert all(item.machine_id and item.fault_mode and item.cost_inr > 0 for item in payload.missed_failures)
