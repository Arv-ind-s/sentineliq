from __future__ import annotations

import sqlite3

from scripts.migrate import migrate


def _legacy_database(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE machines (id TEXT PRIMARY KEY);
            CREATE TABLE predictions (
                id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, ts BIGINT NOT NULL,
                p_failure REAL NOT NULL, rul_p10 REAL NOT NULL, rul_p50 REAL NOT NULL, rul_p90 REAL NOT NULL,
                mode_probs JSON NOT NULL, risk REAL NOT NULL, band TEXT NOT NULL, confidence REAL NOT NULL,
                predictor TEXT NOT NULL, model_version TEXT NOT NULL
            );
            CREATE TABLE diagnoses (
                id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL, diagnosis TEXT NOT NULL,
                reasoning JSON NOT NULL, facts JSON NOT NULL, assumptions JSON NOT NULL, limits JSON NOT NULL,
                actions JSON NOT NULL, citations JSON NOT NULL, generated BOOLEAN NOT NULL,
                model TEXT NOT NULL, prompt_version TEXT NOT NULL, generated_at BIGINT NOT NULL
            );
            CREATE TABLE approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_id TEXT NOT NULL, autonomy_level TEXT NOT NULL,
                required_role TEXT NOT NULL, state TEXT NOT NULL, actor TEXT, reason TEXT, decided_at BIGINT
            );
            CREATE TABLE evidence (id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL, kind TEXT NOT NULL, payload JSON NOT NULL);
            CREATE TABLE audit_ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts BIGINT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                subject TEXT NOT NULL, payload_json JSON NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL
            );
            INSERT INTO machines VALUES ('PUMP-01');
            INSERT INTO predictions VALUES ('pred-old', 'PUMP-01', 1, .5, 1, 2, 3, '{}', 50, 'MEDIUM', .8, 'test', 'v1');
            INSERT INTO evidence VALUES ('S1', 'pred-old', 'sensor', '{"value": 1.1}');
            INSERT INTO approvals (prediction_id, autonomy_level, required_role, state) VALUES ('pred-old', 'A0', 'planner', 'pending');
            INSERT INTO approvals (prediction_id, autonomy_level, required_role, state, actor, decided_at)
            VALUES ('pred-pump-01-missing', 'A0', 'planner', 'approved', 'planner', 2);
            """
        )


def test_explicit_migration_preserves_rows_and_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "plant.db"
    _legacy_database(database_path)
    database_url = f"sqlite:///{database_path}"

    first = migrate(database_url, tmp_path / "backups")
    second = migrate(database_url, tmp_path / "backups")

    assert first["schema_version"] == second["schema_version"] == 2
    assert first["superseded_approvals"] == 1
    assert second["superseded_approvals"] == 0
    assert first["legacy_prediction_references_repaired"] == 1
    assert second["legacy_prediction_references_repaired"] == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        assert connection.execute("SELECT state FROM approvals ORDER BY id").fetchall() == [("superseded",), ("approved",)]
        assert connection.execute("SELECT source FROM predictions WHERE id = 'pred-pump-01-missing'").fetchone()[0] == "legacy_reference"
        assert connection.execute("SELECT prediction_id, citation_id FROM evidence").fetchone() == ("pred-old", "S1")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
