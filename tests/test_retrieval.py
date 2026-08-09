from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from core.retrieval import MaintenanceLogRetriever, NumpyVectorStore, VectorHit


ERP_DB = Path(__file__).resolve().parents[1] / "erp" / "erp.db"


def test_numpy_vector_store_round_trips_and_returns_stable_cosine_hits(tmp_path: Path) -> None:
    store = NumpyVectorStore()
    store.add(["a", "b"], np.asarray([[1, 0], [0, 1]], dtype=np.float32))
    before = store.search(np.asarray([1, 0], dtype=np.float32), limit=2)
    path = tmp_path / "logs.npz"
    store.persist(path)
    after = NumpyVectorStore.load(path).search(np.asarray([1, 0], dtype=np.float32), limit=2)

    assert before == after
    assert before[0].item_id == "a"
    assert before[0].similarity == 1.0
    assert path.exists()


def test_search_is_deterministic_capped_and_returns_stable_citations() -> None:
    retriever = MaintenanceLogRetriever(ERP_DB)
    first = retriever.search("bearing vibration", machine_id="PUMP-07")
    second = retriever.search("bearing vibration", machine_id="PUMP-07")

    assert len(first) <= 6
    assert [item.citation_id for item in first] == [f"[M{index}]" for index in range(1, len(first) + 1)]
    assert [(item.citation_id, item.workorder_id, item.score) for item in first] == [(item.citation_id, item.workorder_id, item.score) for item in second]
    assert first[0].workorder_id == "WO-PUMP-07-001"


def test_retrieval_is_warm_under_five_milliseconds_and_offline(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    retriever = MaintenanceLogRetriever(ERP_DB)
    retriever.search("bearing vibration", machine_id="PUMP-07")

    start = time.perf_counter()
    for _ in range(10):
        retriever.search("bearing vibration", machine_id="PUMP-07")
    elapsed_ms = (time.perf_counter() - start) * 1000 / 10

    assert elapsed_ms <= 5.0, f"warm retrieval averaged {elapsed_ms:.3f}ms"
    assert not hasattr(retriever, "llm_client")


def test_press_history_reports_transducer_replacements_and_aggregates() -> None:
    aggregate = MaintenanceLogRetriever(ERP_DB).aggregate_history("PRESS-03")

    assert aggregate.work_order_count == 32
    assert "SNS-01" in aggregate.fault_codes
    assert aggregate.fault_codes["SNS-01"].count >= 2
    assert aggregate.fault_codes["SNS-01"].mean_lead_time_days == 1.0
    assert any(repeat.fault_code == "SNS-01" and repeat.days_apart <= 90 for repeat in aggregate.repeat_failures)
    assert aggregate.mtbf_days is not None
    assert aggregate.mean_cost_gbp > 0
    assert aggregate.mean_downtime_minutes > 0


class FakeVectorStore:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def add(self, item_ids, vectors) -> None:
        self.ids = list(item_ids)

    def search(self, query, limit=8) -> list[VectorHit]:
        return [VectorHit("WO-PUMP-07-001", 0.99)]

    def persist(self, path) -> None:
        return None


def test_vector_store_protocol_can_be_replaced_without_retriever_changes() -> None:
    fake = FakeVectorStore()
    retriever = MaintenanceLogRetriever(ERP_DB, vector_store=fake)
    results = retriever.search("anything", machine_id="PUMP-07")

    assert fake.ids
    assert results[0].workorder_id == "WO-PUMP-07-001"
    assert results[0].similarity == 0.99
