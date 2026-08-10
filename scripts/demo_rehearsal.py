"""Validate and time the two judge-facing SentinelPdM demo moments.

The command has two deliberately separate modes:

``--verify``
    Reads the live API and generated demo notes, then prints the measured
    values that the presenter must use.  It never substitutes a placeholder.

``--interactive``
    Resets the replay clock before every take, waits for the live PUMP-07
    alert, and records the presenter-controlled start/stop duration for each
    moment.  The browser remains the source of truth for what is shown; this
    command is only the stopwatch and contract checker.

The script uses only the Python standard library so it is available in the
same four-command quickstart environment as the rest of the demo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime_paths import runtime_paths

PUMP_CROSSING_RE = re.compile(r"PUMP-07 fused risk crossed tau=([0-9.]+) at ([0-9.]+)s")
MOMENT_ONE_BUDGET_S = 30.0
MOMENT_TWO_BUDGET_S = 20.0
DEFAULT_CUE_WINDOW_S = (40.0, 75.0)
CHOSEN_CLOSERS = ("Safety/governance", "Resilience")


class RehearsalError(RuntimeError):
    """Raised when the live demo contract cannot be verified."""


@dataclass(frozen=True)
class DemoEvidence:
    """Measured values used by the presenter script."""

    crossing_tau: float
    crossing_seconds: float
    floor_risk: float
    floor_rul_days: float
    floor_failure_probability: float
    diagnosis_confidence: float
    trace_stages: tuple[tuple[str, float, str], ...]
    sensor_window: str
    work_order_title: str
    work_order_date: str
    reactive_minutes: int
    fixed_schedule_minutes: int
    predictive_minutes: int
    reduction_pct: float
    avoided_cost_inr: int
    missed_failure_count: int


def _json_response(response: Any) -> Any:
    try:
        return json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RehearsalError("API returned a non-JSON response") from exc


class DemoAPI:
    """Small authenticated client for the rehearsal-only API calls."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body else {})},
        )
        try:
            with self._opener.open(request, timeout=15) as response:
                return _json_response(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except OSError:
                detail = exc.reason
            raise RehearsalError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise RehearsalError(f"cannot reach {self.base_url}: {exc}") from exc

    def login(self) -> None:
        result = self.request("/auth/login", method="POST", payload={"username": self.username, "password": self.password})
        if not isinstance(result, dict) or result.get("username") != self.username:
            raise RehearsalError("API login response did not identify the requested user")

    def reset_replay(self) -> None:
        result = self.request("/stream/reset", method="POST")
        if result.get("message") != "Replay clock reset to t0":
            raise RehearsalError(f"unexpected replay reset response: {result!r}")

    def wait_for_pump_alert(self, timeout_s: float = 90.0) -> float:
        """Start a fresh SSE replay and return seconds until PUMP-07 alerts."""

        request = urllib.request.Request(f"{self.base_url}/stream/events", headers={"Accept": "text/event-stream"})
        started = time.monotonic()
        event_name = ""
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                while time.monotonic() - started < timeout_s:
                    line = response.readline().decode("utf-8")
                    if not line:
                        break
                    if line.startswith("event: "):
                        event_name = line.removeprefix("event: ").strip()
                    elif line.startswith("data: ") and event_name == "alert":
                        try:
                            payload = json.loads(line.removeprefix("data: "))
                        except json.JSONDecodeError:
                            continue
                        if payload.get("machine_id") == "PUMP-07":
                            return time.monotonic() - started
        except (OSError, urllib.error.URLError) as exc:
            raise RehearsalError(f"PUMP-07 SSE observation failed: {exc}") from exc
        raise RehearsalError(f"PUMP-07 alert was not observed within {timeout_s:.0f}s")

    def measured_evidence(self) -> DemoEvidence:
        health = self.request("/health")
        dependencies = health.get("dependencies", []) if isinstance(health, dict) else []
        if len(dependencies) != 6:
            raise RehearsalError(f"expected 6 health dependencies, got {len(dependencies)}")

        notes = (runtime_paths.data_root / "DEMO_NOTES.md").read_text(encoding="utf-8")
        match = PUMP_CROSSING_RE.search(notes)
        if match is None:
            raise RehearsalError("data/DEMO_NOTES.md has no measured PUMP-07 crossing")
        crossing_tau = float(match.group(1))
        crossing_seconds = float(match.group(2))

        floor = self.request("/machines/PUMP-07")
        prediction = self.request("/machines/PUMP-07/prediction")
        diagnosis_response = self.request("/machines/PUMP-07/diagnose", method="POST", payload={"force_refresh": False})
        trace_id = diagnosis_response.get("trace_id")
        diagnosis = diagnosis_response.get("diagnosis", {})
        trace = self.request(f"/runs/{trace_id}")
        trace_stages = tuple(
            (str(item.get("agent", "unknown")), float(item.get("latency_ms", 0.0)), str(item.get("state", "unknown")))
            for item in trace.get("runs", [])
        )
        if not trace_stages:
            raise RehearsalError("diagnosis produced no persisted trace stages")

        citations = {str(item.get("id")): item for item in diagnosis.get("citations", [])}
        sensor = citations.get("S2")
        work_order = citations.get("M3")
        if not sensor or not sensor.get("t0") or not sensor.get("t1") or not work_order or not work_order.get("date"):
            raise RehearsalError("diagnosis evidence is missing S2 or M3 citation details")

        ledger = self.request("/ledger")
        policies = {str(item.get("policy")): item for item in ledger.get("policies", [])}
        required_policies = {"reactive", "fixed_schedule", "predictive"}
        if set(policies) != required_policies:
            raise RehearsalError(f"ledger policies changed: {sorted(policies)}")

        return DemoEvidence(
            crossing_tau=crossing_tau,
            crossing_seconds=crossing_seconds,
            floor_risk=float(floor["risk"]),
            floor_rul_days=float(floor["rul_days"]),
            floor_failure_probability=float(floor["p_failure_24h"]),
            diagnosis_confidence=float(diagnosis["confidence"]),
            trace_stages=trace_stages,
            sensor_window=f"{sensor['t0']} → {sensor['t1']}",
            work_order_title=str(work_order["title"]),
            work_order_date=str(work_order["date"]),
            reactive_minutes=int(policies["reactive"]["downtime_minutes"]),
            fixed_schedule_minutes=int(policies["fixed_schedule"]["downtime_minutes"]),
            predictive_minutes=int(policies["predictive"]["downtime_minutes"]),
            reduction_pct=float(ledger["reduction_pct"]),
            avoided_cost_inr=int(policies["predictive"]["avoided_cost_inr"]),
            missed_failure_count=len(ledger.get("missed_failures", [])),
        )


def render_script(evidence: DemoEvidence) -> str:
    """Render the measured presenter script, including only observed values."""

    stage_text = " · ".join(f"{agent} {latency:.1f} ms ({state})" for agent, latency, state in evidence.trace_stages)
    avoided_cost = format_inr(evidence.avoided_cost_inr)
    return f'''FRAME (15s)
Plants lose money to unexpected failures, and fault diagnosis needs an expert who isn't there at 3am.
Every number you're about to see is computed by a deterministic model. The AI only explains numbers that
already exist, and it has to cite its evidence.

MOMENT #1 — It sees it coming, and it shows its work. (budget: ≤30s after the alert)
1. The replay starts from t0. PUMP-07's measured crossing is {evidence.crossing_seconds:.1f}s at τ={evidence.crossing_tau:.6f}.
2. Select PUMP-07: the floor shows risk {evidence.floor_risk:.0f}/100, {evidence.floor_failure_probability:.1%} failure probability in 24h,
   and {evidence.floor_rul_days:.1f} days RUL. Diagnosis confidence is {evidence.diagnosis_confidence:.0%}.
3. One click opens Diagnosis. The persisted trace is: {stage_text}. No absent stage is inferred.
4. Hover [S2] to shade the exact six-hour sensor window ({evidence.sensor_window}). Hover [M3] to show
   “{evidence.work_order_title}” dated {evidence.work_order_date}.
5. SAY: “Not a black box. Every sentence is anchored to data you can see.”

MOMENT #2 — The 20% is computed, not claimed. (budget: ≤20s)
1. Three measured bars: Reactive {evidence.reactive_minutes:,} min · Fixed-schedule {evidence.fixed_schedule_minutes:,} min ·
   Predictive {evidence.predictive_minutes:,} min.
2. The measured headline is {evidence.reduction_pct:.2f}% downtime reduction / {avoided_cost} avoided, against the 20% target.
3. Toggle “what the model missed”: {evidence.missed_failure_count} missed failures remain visible with their charged cost.
4. SAY: “Same event stream, three policies, and we charge ourselves a full planned window for every false positive.
   This is the pytest gate, not a slide.”

CLOSERS REHEARSED FOR THIS RUN
- Safety/governance: approve A0 → evidence drawer → ERP work order → /audit/verify. SAY: “No LLM holds a write tool.”
- Resilience: flip the LLM-offline toggle live. SAY: “Resilience you can watch beats resilience described.”
'''


def format_inr(value: int) -> str:
    """Format whole rupees with the Indian lakh/crore grouping convention."""

    if value < 0:
        raise ValueError("INR amount must be non-negative")
    digits = str(value)
    if len(digits) <= 3:
        return f"₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while head:
        groups.append(head[-2:])
        head = head[:-2]
    return f"₹{','.join(reversed(groups))},{tail}"


def _time_take(label: str, budget_s: float) -> float:
    input(f"{label}: press Enter when the timed segment starts… ")
    started = time.monotonic()
    input("Press Enter immediately after the payoff line is spoken… ")
    elapsed = time.monotonic() - started
    status = "PASS" if elapsed <= budget_s else "FAIL"
    print(f"{label}: {elapsed:.2f}s / {budget_s:.0f}s [{status}]")
    return elapsed


def _cue_window() -> tuple[float, float]:
    """Read the configured demo cue window without adding a YAML dependency."""

    try:
        config = (ROOT / "config.yaml").read_text(encoding="utf-8")
        match = re.search(r"demo_alert_window_s:\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]", config)
        if match:
            return float(match.group(1)), float(match.group(2))
    except OSError:
        pass
    return DEFAULT_CUE_WINDOW_S


def interactive_rehearsal(api: DemoAPI, evidence: DemoEvidence, moment: str, repeats: int) -> int:
    print(render_script(evidence))
    failures = 0
    cue_lower, cue_upper = _cue_window()
    for take in range(1, repeats + 1):
        if moment in {"1", "all"}:
            api.reset_replay()
            input(f"Take {take}, moment #1: reload Live Floor, deliver the framing line from memory, then press Enter to arm the alert cue… ")
            observed = api.wait_for_pump_alert()
            print(f"PUMP-07 alert observed after {observed:.2f}s; start speaking the moment now.")
            if observed < cue_lower or observed > cue_upper:
                print(f"Moment #1 cue: FAIL (outside the configured {cue_lower:.0f}–{cue_upper:.0f}s replay window)")
                failures += 1
            if _time_take(f"Moment #1 take {take}", MOMENT_ONE_BUDGET_S) > MOMENT_ONE_BUDGET_S:
                failures += 1
        if moment in {"2", "all"}:
            api.reset_replay()
            input(f"Take {take}, moment #2: open Downtime Ledger, deliver the framing line from memory, then press Enter to start timing… ")
            if _time_take(f"Moment #2 take {take}", MOMENT_TWO_BUDGET_S) > MOMENT_TWO_BUDGET_S:
                failures += 1
        # Leave the application in a deterministic state even after the last
        # take; the next operator action starts from a fresh t0.
        api.reset_replay()
    for closer in CHOSEN_CLOSERS:
        input(f"Closer {closer}: say the chosen line from memory, then press Enter to record the rehearsal… ")
        print(f"Closer {closer}: recorded")
    return failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("SENTINEL_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("DEMO_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", "fixture"))
    parser.add_argument("--verify", action="store_true", help="verify live evidence and print the measured script")
    parser.add_argument("--interactive", action="store_true", help="run the presenter-controlled stopwatch")
    parser.add_argument("--moment", choices=("1", "2", "all"), default="all")
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    if not args.verify and not args.interactive:
        args.verify = True
    api = DemoAPI(args.api_url, args.username, args.password)
    try:
        api.login()
        evidence = api.measured_evidence()
        if args.interactive:
            return interactive_rehearsal(api, evidence, args.moment, args.repeats)
        print(render_script(evidence))
    except (OSError, RehearsalError, ValueError) as exc:
        print(f"demo rehearsal failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
