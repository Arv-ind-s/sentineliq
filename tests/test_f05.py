from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from scripts.demo_rehearsal import DemoEvidence, render_script


def _evidence() -> DemoEvidence:
    return DemoEvidence(
        crossing_tau=0.42,
        crossing_seconds=52.3,
        floor_risk=62.0,
        floor_rul_days=6.2,
        floor_failure_probability=0.496,
        diagnosis_confidence=0.87,
        trace_stages=(("coordinator", 4.0, "fallback"),),
        sensor_window="2026-01-01T06:00:00Z → 2026-01-01T12:00:00Z",
        work_order_title="Drive-end bearing replacement",
        work_order_date="2025-09-27T12:00:00Z",
        reactive_minutes=19_200,
        fixed_schedule_minutes=21_900,
        predictive_minutes=10_230,
        reduction_pct=46.72,
        avoided_cost_inr=49_133_636,
        missed_failure_count=21,
    )


def test_f05_script_uses_measured_values_and_honest_trace() -> None:
    script = render_script(_evidence())

    for marker in ("52.3s", "19,200", "21,900", "10,230", "46.72%", "₹4,91,33,636", "21 missed failures", "coordinator 4.0 ms"):
        assert marker in script
    assert "340 ms" not in script
    assert "4.2 days" not in script
    assert "30.9%" not in script
    assert "£" not in script


def test_stream_reset_is_admin_only_and_resets_the_replay_clock() -> None:
    planner = TestClient(app)
    assert planner.post("/auth/login", json={"username": "planner", "password": "fixture"}).status_code == 200
    assert planner.post("/stream/reset").status_code == 403

    admin = TestClient(app)
    assert admin.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200
    response = admin.post("/stream/reset")
    assert response.status_code == 200
    assert response.json() == {"message": "Replay clock reset to t0"}
