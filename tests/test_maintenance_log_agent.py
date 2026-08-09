from __future__ import annotations

import json
import time
from pathlib import Path

from agents.base import RunContext
from agents.maintenance_log_agent import ERPTransportError, MaintenanceLogAgent
from core.retrieval import MaintenanceLogRetriever
from llm.client import LLMResponse


ERP_DB = Path(__file__).resolve().parents[1] / "erp" / "erp.db"


class FakeERP:
    def __init__(self, rows: list[dict[str, object]], *, down: bool = False) -> None:
        self.rows = rows
        self.down = down
        self.workorder_calls = 0
        self.parts_calls = 0

    def search_workorders(self, machine_id: str, since=None, limit: int = 6) -> list[dict[str, object]]:
        self.workorder_calls += 1
        if self.down:
            raise ERPTransportError("ERP unavailable")
        return list(self.rows[:limit])

    def parts_lookup(self, fault_code: str) -> list[dict[str, object]]:
        self.parts_calls += 1
        if self.down:
            raise ERPTransportError("ERP unavailable")
        return [{"part_id": "BRG-KIT", "name": "Bearing kit", "fault_code": fault_code, "lead_time_days": 2}]


class FakeLLM:
    provider = "test"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, model_cfg) -> LLMResponse:
        self.calls += 1
        payload = {
            "recurring_modes": ["bearing wear recurs [M1]"],
            "effective_repairs": ["Replaced the bearing [M1]"],
            "ineffective_repairs": [],
            "parts_needed": ["Bearing kit [M1]"],
            "typical_downtime_min": 120,
            "notes": ["Prior work order confirms the repair [M1]"],
        }
        return LLMResponse(json.dumps(payload), 10, 20, "test-model", 1.0, provider="test")


def _live_rows() -> list[dict[str, object]]:
    return [
        {
            "workorder_id": "WO-PUMP-07-001",
            "asset_id": "PUMP-07",
            "fault_code": "BRG-01",
            "description": "Replaced drive-end bearing",
            "technician_notes": "Bearing replacement completed",
            "created_at": "2025-01-01T00:00:00+00:00",
            "cost_gbp": 1800,
            "downtime_minutes": 120,
            "lead_time_days": 2,
        }
    ]


def test_live_path_retrieval_parts_and_exactly_one_llm_call() -> None:
    erp = FakeERP(_live_rows())
    llm = FakeLLM()
    agent = MaintenanceLogAgent(
        erp_client=erp,
        retriever=MaintenanceLogRetriever(ERP_DB),
        llm_client=llm,
        llm_api_key="test-key",
        retry_delays_s=(0, 0, 0),
    )

    result = agent.run(RunContext(payload={"machine_id": "PUMP-07", "query": "bearing vibration"}))

    assert result.ok is True
    assert result.output["source"] == "live"
    assert llm.calls == 1
    assert erp.workorder_calls == 1
    assert erp.parts_calls >= 1
    assert result.output["summary"]["parts_needed"][0]["citations"] == ["[M1]"]


def test_unset_llm_key_uses_deterministic_structured_summary() -> None:
    llm = FakeLLM()
    agent = MaintenanceLogAgent(
        erp_client=FakeERP(_live_rows()),
        retriever=MaintenanceLogRetriever(ERP_DB),
        llm_client=llm,
        llm_api_key="",
        retry_delays_s=(0, 0, 0),
    )

    result = agent.run(RunContext(payload={"machine_id": "PUMP-07", "query": "bearing vibration"}))

    assert result.ok is True
    assert result.output["summary"]["recurring_modes"]
    assert llm.calls == 0
    assert all(item["citations"] for item in result.output["summary"]["recurring_modes"])


def test_new_live_work_order_precedes_stale_retrieval_index() -> None:
    live = _live_rows()
    live[0] = {**live[0], "workorder_id": "WO-NEW-LIVE", "created_at": "2026-08-08T09:00:00+00:00"}
    agent = MaintenanceLogAgent(
        erp_client=FakeERP(live),
        retriever=MaintenanceLogRetriever(ERP_DB),
        llm_api_key="",
        retry_delays_s=(0, 0, 0),
    )

    result = agent.run(RunContext(payload={"machine_id": "PUMP-07", "query": "bearing vibration"}))

    assert result.output["source"] == "live"
    assert result.output["evidence"][0]["workorder_id"] == "WO-NEW-LIVE"
    assert len({item["workorder_id"] for item in result.output["evidence"]}) == result.output["retrieved_count"]


def test_down_erp_uses_local_cache_under_two_seconds_and_opens_breaker() -> None:
    erp = FakeERP(_live_rows(), down=True)
    llm = FakeLLM()
    agent = MaintenanceLogAgent(
        erp_client=erp,
        retriever=MaintenanceLogRetriever(ERP_DB),
        llm_client=llm,
        llm_api_key="test-key",
        retry_delays_s=(0, 0, 0),
    )

    started = time.perf_counter()
    result = agent.run(RunContext(payload={"machine_id": "PUMP-07", "query": "bearing vibration"}))
    elapsed = time.perf_counter() - started

    assert result.ok is True
    assert result.output["source"] == "cache"
    assert result.output["retrieved_count"] > 0
    assert result.provider == "local"
    assert llm.calls == 0
    assert elapsed < 2.0
    calls_after_open = erp.workorder_calls
    agent.run(RunContext(payload={"machine_id": "PUMP-07", "query": "bearing vibration"}))
    assert erp.workorder_calls == calls_after_open


def test_zero_machine_history_returns_assumption_labelled_fleet_priors() -> None:
    agent = MaintenanceLogAgent(
        erp_client=FakeERP([]),
        retriever=MaintenanceLogRetriever(ERP_DB),
        retry_delays_s=(0, 0, 0),
    )

    result = agent.run(RunContext(payload={"machine_id": "NEW-ASSET", "query": "maintenance history"}))
    summary = result.output["summary"]

    assert result.ok is True
    assert summary["recurring_modes"]
    assert all(item["kind"] == "assumption" for item in summary["recurring_modes"])
    assert summary["typical_downtime_min"]["kind"] == "assumption"
    assert all(item["kind"] == "assumption" for item in summary["notes"])
