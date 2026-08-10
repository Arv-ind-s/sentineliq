"""Run the B-03 Gemini smoke test and write the H1.4 latency decision."""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, settings
from llm.client import _call_json, create_client, failure_reason


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _branch(tokens_per_second: float | None) -> str:
    if tokens_per_second is None:
        return "offline_template"
    if tokens_per_second >= 150:
        return "two_llm_calls"
    if tokens_per_second >= 60:
        return "one_llm_call_no_log_summary"
    return "one_llm_call_coordinator_300_on_demand"


def run_smoke(output_path: str | Path = ROOT / "eval" / "latency_baseline.json") -> dict[str, object]:
    client = create_client()
    model_cfg = {"name": settings.llm.models.coordinator.name, "temperature": 0.0, "max_tokens": 300}
    messages = [{"role": "user", "content": "Return a JSON object with one key named status and value ok."}]
    started = time.perf_counter()
    try:
        response = client.chat(messages, model_cfg)
        elapsed_s = max(time.perf_counter() - started, 1e-9)
        observed_tokens = response.completion_tokens
        tokens_per_second = observed_tokens / elapsed_s if observed_tokens else 0.0
        result: dict[str, object] = {
            "measured_at": datetime.now(UTC).isoformat(),
            "reachable": True,
            "reason": None,
            "model": response.model,
            "provider": response.provider,
            "observed_output_tokens": observed_tokens,
            "observed_output_tokens_per_second": round(tokens_per_second, 3),
            "first_token_latency_ms": round(response.latency_ms, 3),
            "branch_taken": _branch(tokens_per_second),
        }
        logging.info("Gemini API reachable provider=%s output_tok_s=%.1f branch=%s", response.provider, tokens_per_second, result["branch_taken"])
    except BaseException as error:
        reason = failure_reason(error)
        result = {
            "measured_at": datetime.now(UTC).isoformat(),
            "reachable": False,
            "reason": reason,
            "model": settings.llm.models.coordinator.name,
            "provider": getattr(client, "provider", settings.llm.provider),
            "observed_output_tokens": None,
            "observed_output_tokens_per_second": None,
            "first_token_latency_ms": None,
            "branch_taken": "offline_template",
        }
        logging.warning("Gemini API unavailable reason=%s; continuing with deterministic templates", reason)
    finally:
        client.close()
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    run_smoke()
