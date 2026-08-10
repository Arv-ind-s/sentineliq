"""Offline-first retrieval and structured aggregation over ERP work orders.

TF-IDF is the MVP embedding path by design.  ``NumpyVectorStore`` is the
small, dependency-free swap point for a future dense embedding provider or
ANN index; callers depend only on the ``VectorStore`` protocol.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from core.redaction import injection_scan, redact
from core.runtime_paths import runtime_paths


DEFAULT_ERP_DB = runtime_paths.erp_db
DEFAULT_INDEX_PATH = runtime_paths.index_path
MAX_FEATURES = 4_000
TOP_SIMILAR = 8
MAX_RESULTS = 6
LOOKBACK_DAYS = 180
REPEAT_WINDOW_DAYS = 90
RECENCY_HALF_LIFE_DAYS = 90
SIMILARITY_WEIGHT = 0.5
RECENCY_WEIGHT = 0.3
SAME_MODEL_WEIGHT = 0.2


@dataclass(frozen=True)
class VectorHit:
    item_id: str
    similarity: float


class VectorStore(Protocol):
    """Protocol for exact or approximate vector indexes."""

    def add(self, item_ids: Sequence[str], vectors: np.ndarray) -> None: ...

    def search(self, query: np.ndarray, limit: int = TOP_SIMILAR) -> list[VectorHit]: ...

    def persist(self, path: str | Path) -> None: ...

    @classmethod
    def load(cls, path: str | Path) -> "VectorStore": ...


class NumpyVectorStore:
    """Exact cosine search over a float32 matrix.

    At the MVP's 480 records this is both faster and easier to audit than an
    ANN service.  IDs are sorted as a final tie-breaker, making results stable
    across identical calls and Python processes.
    """

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension
        self._item_ids: list[str] = []
        self._vectors = np.empty((0, dimension or 0), dtype=np.float32)

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(self._item_ids)

    def add(self, item_ids: Sequence[str], vectors: np.ndarray) -> None:
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.ndim != 2 or len(item_ids) != matrix.shape[0]:
            raise ValueError("vector IDs and matrix rows must have matching dimensions")
        if not np.isfinite(matrix).all():
            raise ValueError("vector matrix must contain only finite values")
        if self.dimension is None:
            self.dimension = int(matrix.shape[1])
            self._vectors = np.empty((0, self.dimension), dtype=np.float32)
        if matrix.shape[1] != self.dimension:
            raise ValueError(f"vector dimension mismatch: expected {self.dimension}, got {matrix.shape[1]}")
        incoming_ids = [str(item_id) for item_id in item_ids]
        if len(set(incoming_ids)) != len(incoming_ids) or set(incoming_ids).intersection(self._item_ids):
            raise ValueError("vector IDs must be unique within and across additions")
        self._item_ids.extend(incoming_ids)
        self._vectors = np.vstack((self._vectors, matrix))

    def search(self, query: np.ndarray, limit: int = TOP_SIMILAR) -> list[VectorHit]:
        if limit <= 0 or not self._item_ids:
            return []
        vector = np.asarray(query, dtype=np.float32).reshape(-1)
        if self.dimension is None or vector.shape[0] != self.dimension:
            raise ValueError(f"query dimension mismatch: expected {self.dimension}, got {vector.shape[0]}")
        query_norm = float(np.linalg.norm(vector))
        if query_norm == 0:
            return []
        norms = np.linalg.norm(self._vectors, axis=1)
        denominator = norms * query_norm
        similarities = np.divide(self._vectors @ vector, denominator, out=np.zeros_like(norms), where=denominator > 0)
        ranked = sorted(
            range(len(self._item_ids)),
            key=lambda index: (-float(similarities[index]), self._item_ids[index]),
        )[:limit]
        return [VectorHit(self._item_ids[index], float(similarities[index])) for index in ranked]

    def persist(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(target, item_ids=np.asarray(self._item_ids, dtype=str), vectors=self._vectors)

    @classmethod
    def load(cls, path: str | Path) -> "NumpyVectorStore":
        with np.load(Path(path), allow_pickle=False) as payload:
            vectors = np.asarray(payload["vectors"], dtype=np.float32)
            ids = [str(item_id) for item_id in payload["item_ids"].tolist()]
        store = cls(vectors.shape[1] if vectors.ndim == 2 else None)
        if ids:
            store.add(ids, vectors)
        return store


@dataclass(frozen=True)
class WorkOrderRecord:
    workorder_id: str
    machine_id: str
    machine_model: str
    fault_code: str
    description: str
    technician_notes: str
    created_at: datetime
    cost_gbp: float
    downtime_minutes: int
    lead_time_days: int
    order_type: str
    injection_patterns: tuple[str, ...] = ()

    @property
    def search_text(self) -> str:
        return " ".join((self.fault_code, self.description, self.technician_notes, self.order_type))


@dataclass(frozen=True)
class RetrievedWorkOrder:
    citation_id: str
    record: WorkOrderRecord
    similarity: float
    score: float

    @property
    def workorder_id(self) -> str:
        return self.record.workorder_id

    @property
    def machine_id(self) -> str:
        return self.record.machine_id

    @property
    def fault_code(self) -> str:
        return self.record.fault_code


@dataclass(frozen=True)
class FaultCodeAggregate:
    fault_code: str
    count: int
    mtbf_days: float | None
    mean_cost_gbp: float
    mean_downtime_minutes: float
    mean_lead_time_days: float


@dataclass(frozen=True)
class RepeatFailure:
    fault_code: str
    first_workorder_id: str
    second_workorder_id: str
    first_at: datetime
    second_at: datetime
    days_apart: float


@dataclass(frozen=True)
class HistoryAggregate:
    machine_id: str
    work_order_count: int
    mtbf_days: float | None
    mean_cost_gbp: float
    mean_downtime_minutes: float
    part_lead_times: dict[str, float]
    fault_codes: dict[str, FaultCodeAggregate]
    repeat_failures: tuple[RepeatFailure, ...]

    @property
    def dominant_fault_code(self) -> str | None:
        return max(self.fault_codes.values(), key=lambda item: item.count).fault_code if self.fault_codes else None


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _mean_interval(rows: Sequence[WorkOrderRecord]) -> float | None:
    timestamps = sorted(row.created_at for row in rows)
    if len(timestamps) < 2:
        return None
    return float(np.mean([(right - left).total_seconds() / 86_400 for left, right in zip(timestamps, timestamps[1:])]))


class MaintenanceLogRetriever:
    """Hybrid TF-IDF retrieval over the local ERP database."""

    def __init__(self, database_path: str | Path = DEFAULT_ERP_DB, vector_store: VectorStore | None = None) -> None:
        self.database_path = Path(database_path)
        self.records = self._load_records()
        self._by_id = {record.workorder_id: record for record in self.records}
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=MAX_FEATURES)
        matrix = self.vectorizer.fit_transform([record.search_text for record in self.records]).toarray().astype(np.float32)
        self.vector_store = vector_store or NumpyVectorStore(matrix.shape[1])
        self.vector_store.add([record.workorder_id for record in self.records], matrix)
        self.reference_at = max((record.created_at for record in self.records), default=datetime.now(UTC))

    def _load_records(self) -> list[WorkOrderRecord]:
        query = """
            SELECT wo.workorder_id, wo.asset_id, a.model, wo.fault_code, wo.description,
                   wo.technician_notes, wo.created_at, wo.cost_gbp, wo.downtime_minutes,
                   wo.lead_time_days, wo.order_type
            FROM work_orders AS wo
            LEFT JOIN assets AS a ON a.asset_id = wo.asset_id
            ORDER BY wo.workorder_id
        """
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(query).fetchall()
        records: list[WorkOrderRecord] = []
        for row in rows:
            description = str(row[4])
            technician_notes = str(row[5] or "")
            scan = injection_scan(f"{description}\n{technician_notes}")
            records.append(
                WorkOrderRecord(
                    workorder_id=str(row[0]),
                    machine_id=str(row[1]),
                    machine_model=str(row[2] or ""),
                    fault_code=str(row[3]),
                    description=redact(description),
                    technician_notes=redact(technician_notes),
                    created_at=_parse_timestamp(str(row[6])),
                    cost_gbp=float(row[7] or 0),
                    downtime_minutes=int(row[8] or 0),
                    lead_time_days=int(row[9] or 0),
                    order_type=str(row[10] or "corrective"),
                    injection_patterns=scan.matched_patterns,
                )
            )
        return records

    @staticmethod
    def _recency(created_at: datetime, reference_at: datetime) -> float:
        age_days = max(0.0, (reference_at - created_at).total_seconds() / 86_400)
        return math.exp(-math.log(2.0) * age_days / RECENCY_HALF_LIFE_DAYS)

    def _candidate_ids(self, query: str, machine_id: str | None, fault_code: str | None) -> dict[str, float]:
        query_vector = self.vectorizer.transform([query]).toarray().astype(np.float32)[0]
        similarities = {hit.item_id: hit.similarity for hit in self.vector_store.search(query_vector, TOP_SIMILAR)}
        candidate_ids = set(similarities)
        cutoff = self.reference_at.timestamp() - LOOKBACK_DAYS * 86_400
        for record in self.records:
            if machine_id and record.machine_id == machine_id and record.created_at.timestamp() >= cutoff:
                candidate_ids.add(record.workorder_id)
            if fault_code and record.fault_code == fault_code:
                candidate_ids.add(record.workorder_id)
        return {item_id: similarities.get(item_id, 0.0) for item_id in candidate_ids}

    def search(
        self,
        query: str,
        *,
        machine_id: str | None = None,
        fault_code: str | None = None,
        limit: int = MAX_RESULTS,
    ) -> list[RetrievedWorkOrder]:
        """Return a stable hybrid-ranked set of at most six cited work orders."""
        if not query.strip() and not machine_id and not fault_code:
            return []
        limit = min(MAX_RESULTS, max(0, int(limit)))
        candidate_similarities = self._candidate_ids(query, machine_id, fault_code)
        target_model = next((record.machine_model for record in self.records if record.machine_id == machine_id), "")
        ranked: list[tuple[float, float, WorkOrderRecord]] = []
        for item_id, similarity in candidate_similarities.items():
            record = self._by_id[item_id]
            # Machine-scoped candidates are included for context, but the
            # model-match bonus must not make an unrelated recent record beat
            # a strongly matching historical incident.
            same_model = 1.0 if target_model and record.machine_model == target_model and similarity > 0 else 0.0
            score = (
                SIMILARITY_WEIGHT * similarity
                + RECENCY_WEIGHT * self._recency(record.created_at, self.reference_at)
                + SAME_MODEL_WEIGHT * same_model
            )
            ranked.append((score, similarity, record))
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2].created_at.timestamp(), item[2].workorder_id))
        return [RetrievedWorkOrder(f"[M{index}]", record, similarity, score) for index, (score, similarity, record) in enumerate(ranked[:limit], 1)]

    def retrieve(self, query: str, **kwargs: object) -> list[RetrievedWorkOrder]:
        """Compatibility alias for agent callers that use retrieval terminology."""
        return self.search(query, **kwargs)

    def aggregate_history(self, machine_id: str, fault_code: str | None = None) -> HistoryAggregate:
        """Return SQL-selected maintenance facts and deterministic aggregates."""
        clauses = ["wo.asset_id = ?"]
        parameters: list[object] = [machine_id]
        if fault_code:
            clauses.append("wo.fault_code = ?")
            parameters.append(fault_code)
        query = f"""
            SELECT wo.workorder_id, wo.asset_id, a.model, wo.fault_code, wo.description,
                   wo.technician_notes, wo.created_at, wo.cost_gbp, wo.downtime_minutes,
                   wo.lead_time_days, wo.order_type
            FROM work_orders AS wo
            LEFT JOIN assets AS a ON a.asset_id = wo.asset_id
            WHERE {' AND '.join(clauses)}
            ORDER BY wo.created_at, wo.workorder_id
        """
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(query, parameters).fetchall()
            summary = connection.execute(
                f"SELECT COUNT(*), AVG(wo.cost_gbp), AVG(wo.downtime_minutes) FROM work_orders AS wo WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
            sql_fault_stats = connection.execute(
                f"""
                SELECT wo.fault_code, COUNT(*), AVG(wo.cost_gbp), AVG(wo.downtime_minutes), AVG(wo.lead_time_days)
                FROM work_orders AS wo
                WHERE {' AND '.join(clauses)}
                GROUP BY wo.fault_code
                """,
                parameters,
            ).fetchall()
        records = [
            WorkOrderRecord(str(row[0]), str(row[1]), str(row[2] or ""), str(row[3]), str(row[4]), str(row[5] or ""), _parse_timestamp(str(row[6])), float(row[7] or 0), int(row[8] or 0), int(row[9] or 0), str(row[10] or "corrective"))
            for row in rows
        ]
        grouped: dict[str, list[WorkOrderRecord]] = {}
        for record in records:
            grouped.setdefault(record.fault_code, []).append(record)
        sql_stats_by_code = {str(row[0]): row for row in sql_fault_stats}
        fault_stats = {
            code: FaultCodeAggregate(code, int(sql_stats_by_code[code][1]), _mean_interval(group), float(sql_stats_by_code[code][2] or 0), float(sql_stats_by_code[code][3] or 0), float(sql_stats_by_code[code][4] or 0))
            for code, group in grouped.items()
        }
        repeats: list[RepeatFailure] = []
        for code, group in grouped.items():
            ordered = sorted(group, key=lambda row: (row.created_at, row.workorder_id))
            for first, second in zip(ordered, ordered[1:]):
                days_apart = (second.created_at - first.created_at).total_seconds() / 86_400
                if 0 <= days_apart <= REPEAT_WINDOW_DAYS:
                    repeats.append(RepeatFailure(code, first.workorder_id, second.workorder_id, first.created_at, second.created_at, days_apart))
        repeats.sort(key=lambda item: (item.days_apart, item.fault_code, item.first_workorder_id))
        part_lead_times = {code: stats.mean_lead_time_days for code, stats in fault_stats.items()}
        return HistoryAggregate(
            machine_id=machine_id,
            work_order_count=int(summary[0] or 0),
            mtbf_days=_mean_interval(records),
            mean_cost_gbp=float(summary[1] or 0),
            mean_downtime_minutes=float(summary[2] or 0),
            part_lead_times=part_lead_times,
            fault_codes=fault_stats,
            repeat_failures=tuple(repeats),
        )


__all__ = [
    "FaultCodeAggregate",
    "HistoryAggregate",
    "MaintenanceLogRetriever",
    "NumpyVectorStore",
    "RepeatFailure",
    "RetrievedWorkOrder",
    "VectorHit",
    "VectorStore",
    "WorkOrderRecord",
]
