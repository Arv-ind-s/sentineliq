"""Coordinator agent for grounded, evidence-led diagnosis.

The coordinator is deliberately a thin narrative layer.  It computes the
deterministic fusion decision before constructing a prompt, and it never lets
the language model change that decision.  The module is also usable offline:
when the Gemini API is unavailable (or no key is configured), the same strict
output contract is populated by a deterministic template.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agents.base import AgentResult, RunContext
from core.config import settings
from core.currency import gbp_to_inr
from core.fusion import (
    FusionDecision,
    HistoryEvidence,
    MachineContext,
    SensorEvidence,
    explain,
    fuse,
)
from core.redaction import redact, wrap_untrusted_document
from llm.client import LLMResponse, create_client, parse_json


logger = logging.getLogger(__name__)
PROMPT_VERSION = "v1"
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / PROMPT_VERSION / "coordinator.md"
_CITATION_RE = re.compile(r"\[(?P<id>[SM]\d+)\]", re.IGNORECASE)
_CITATION_SUFFIX_RE = re.compile(r"(?:\[[SM]\d+\])+(?:[.!?])?$", re.IGNORECASE)
_VALID_ID_RE = re.compile(r"[SM]\d+", re.IGNORECASE)
_GROUPED_CITATION_RE = re.compile(r"\[((?:[SM]\d+\s*,\s*)+[SM]\d+)\]", re.IGNORECASE)
_CONTEXT_CITATION_RE = re.compile(r"\[(fixed_fusion|sensor_result|history_result)\]", re.IGNORECASE)
_CORE_FIELDS = {
    "fault_diagnosis",
    "reasoning_steps",
    "facts",
    "assumptions",
    "recommended_actions",
    "limits",
    "citations",
}


class CoordinatorModel(BaseModel):
    """Base for all Coordinator models; unknown model output is rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)


class CoordinatorAction(CoordinatorModel):
    """One grounded maintenance recommendation."""

    action: str = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    rationale: str = Field(min_length=1)
    parts: list[str]
    est_labour_hours: float = Field(ge=0)
    window: str = Field(min_length=1)
    citations: list[str] = Field(min_length=1)

    @field_validator("citations")
    @classmethod
    def citation_identifiers_are_well_formed(cls, value: list[str]) -> list[str]:
        normalised = [_normalise_id(item) for item in value]
        if any(item is None for item in normalised):
            raise ValueError("action citations must use S# or M# identifiers")
        return [item for item in normalised if item is not None]


class CoordinatorDraft(CoordinatorModel):
    """Strict narrative contract described by spec §3.8.3.

    ``citations`` on actions is retained in the implementation contract even
    though the compact specification skeleton shows citations inline in the
    rationale.  It makes citation resolution explicit for downstream routing
    and matches the existing guardrail/API contract.
    """

    fault_diagnosis: str = Field(min_length=1)
    reasoning_steps: list[str] = Field(min_length=3, max_length=5)
    facts: list[str]
    assumptions: list[str]
    recommended_actions: list[CoordinatorAction] = Field(min_length=1, max_length=4)
    limits: str = Field(min_length=1)
    citations: list[str]

    @field_validator("fault_diagnosis")
    @classmethod
    def diagnosis_is_at_most_sixty_words(cls, value: str) -> str:
        if len(value.split()) > 60:
            raise ValueError("fault_diagnosis must contain at most 60 words")
        return value

    @field_validator("reasoning_steps")
    @classmethod
    def reasoning_ends_with_citations(cls, value: list[str]) -> list[str]:
        if any(not _CITATION_SUFFIX_RE.search(item.strip()) for item in value):
            raise ValueError("every reasoning step must end with one or more citation tags")
        return value

    @field_validator("citations")
    @classmethod
    def citations_are_well_formed(cls, value: list[str]) -> list[str]:
        normalised = [_normalise_id(item) for item in value]
        if any(item is None for item in normalised):
            raise ValueError("citations must use S# or M# identifiers")
        return [item for item in normalised if item is not None]


Band = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "NODATA"]
Autonomy = Literal["A0", "A1", "A2", "A3"]


class CoordinatorOutput(CoordinatorDraft):
    """Validated Coordinator result, including fixed fusion and provenance."""

    risk: float = Field(ge=0, le=100)
    band: Band
    confidence: float = Field(ge=0, le=1)
    autonomy: Autonomy
    action_class: str = Field(min_length=1)
    scheduling_window: str = Field(min_length=1)
    fusion_terms: dict[str, float]
    generated: bool
    model: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    dropped_evidence: list[str]
    evidence_tokens: int = Field(ge=0)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalise_id(value: Any) -> str | None:
    match = _VALID_ID_RE.fullmatch(str(value).strip().strip("[]"))
    return match.group(0).upper() if match else None


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(child) for child in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    return str(value)


def _token_count(value: Any) -> int:
    """Use the same conservative word-based estimate as the LLM adapter."""

    if not value:
        return 0
    if isinstance(value, str):
        return max(1, len(value.split()))
    return _token_count(json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")))


def _prediction_mode(sensor: Mapping[str, Any]) -> str:
    probabilities = sensor.get("mode_probs", sensor.get("fault_mode_probs", {}))
    if isinstance(probabilities, Mapping) and probabilities:
        return str(max(probabilities.items(), key=lambda item: float(item[1]))[0])
    return str(sensor.get("predicted_mode", sensor.get("fault_mode", sensor.get("mode", "unknown"))))


def _sensor_input(value: Any) -> SensorEvidence:
    sensor = _result_output(value)
    quality = sensor.get("quality", {})
    quality_value = _field(quality, "completeness", _field(sensor, "data_quality", _field(sensor, "quality", 0.0)))
    evidence = sensor.get("evidence", [])
    return SensorEvidence(
        p_failure_24h=float(sensor.get("p_failure_24h", sensor.get("p_failure", 0.0))),
        rul_p50_days=float(sensor.get("rul_p50_days", sensor.get("rul_p50", sensor.get("rul_p50_days", 0.0)))),
        predicted_mode=_prediction_mode(sensor),
        data_quality=float(quality_value or 0.0),
        evidence_count=int(sensor.get("evidence_count", len(evidence) if isinstance(evidence, Sequence) else 0)),
        required_channel_suspect=bool(sensor.get("required_channel_suspect", False)),
        sensor_drift_signal=sensor.get("sensor_drift_signal"),
    )


def _history_input(value: Any) -> HistoryEvidence:
    history = _result_output(value)
    summary = history.get("summary", history)
    occurrences = history.get("fault_code_occurrences_days", ())
    recurring = summary.get("recurring_modes", []) if isinstance(summary, Mapping) else []
    dominant_mode = history.get("dominant_mode")
    if dominant_mode is None and isinstance(summary, Mapping):
        dominant_mode = summary.get("dominant_mode", summary.get("dominant_fault_mode"))
    repeat_count = int(history.get("repeat_failure_count", len(recurring) if recurring else 0))
    return HistoryEvidence(
        history_prior=float(history.get("history_prior", 0.0)),
        dominant_mode=str(dominant_mode) if dominant_mode else None,
        repeat_failure=bool(history.get("repeat_failure", False)),
        repeat_failure_count=repeat_count,
        fault_code_occurrences_days=tuple(float(item) for item in occurrences),
        transducer_replacements_90d=int(history.get("transducer_replacements_90d", 0)),
    )


def _result_output(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        nested = value.get("output")
        return dict(nested) if isinstance(nested, Mapping) else dict(value)
    nested = getattr(value, "output", None)
    return dict(nested) if isinstance(nested, Mapping) else {}


def _machine_input(value: Any) -> MachineContext:
    machine = _result_output(value) if not isinstance(value, Mapping) else dict(value)
    return MachineContext(
        criticality=machine.get("criticality", machine.get("criticality_level", "minor")),
        hourly_downtime_cost_gbp=float(machine.get("hourly_downtime_cost_gbp", machine.get("downtime_cost_gbp", 0.0))),
    )


def _evidence_items(payload: Mapping[str, Any], sensor: Any, history: Any) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    supplied = payload.get("evidence_bundle", payload.get("evidence", []))
    if isinstance(supplied, Mapping):
        supplied = supplied.get("items", supplied.get("evidence", []))
    if isinstance(supplied, Sequence) and not isinstance(supplied, (str, bytes)):
        candidates.extend(supplied)
    for value in (sensor, history):
        output = _result_output(value)
        evidence = output.get("evidence", [])
        if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
            candidates.extend(evidence)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(candidates, 1):
        if not isinstance(item, Mapping):
            continue
        row = _json_ready(dict(item))
        identifier = _normalise_id(row.get("id", row.get("citation_id", "")))
        if identifier is None:
            identifier = f"E{index}"
        # Evidence identifiers are the public citation namespace.  Keep only
        # S#/M# records here; E# records remain useful to the prompt but are
        # not allowed to become citations in a diagnosis.
        row["id"] = identifier
        if identifier.startswith("M"):
            for field_name in ("description", "technician_notes", "technician_note"):
                if field_name not in row:
                    continue
                value = str(row[field_name])
                if not value.startswith("<untrusted_document "):
                    row[field_name] = wrap_untrusted_document(redact(value), identifier)
        key = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _rank_key(item: Mapping[str, Any], index: int) -> tuple[float, int]:
    if "rank" in item:
        try:
            # Rank 1 is the strongest result.  Using its inverse keeps the
            # ordering intuitive while still handling a malformed zero rank.
            return (1.0 / max(float(item["rank"]), 1.0), -index)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    for key in ("score", "relevance", "similarity"):
        try:
            return (float(item.get(key, 0.0)), -index)
        except (TypeError, ValueError):
            continue
    return (0.0, -index)


def _trim_evidence(items: Sequence[Mapping[str, Any]], budget: int) -> tuple[list[dict[str, Any]], list[str], int]:
    """Keep the highest-ranked evidence until the hard token cap is met."""

    ordered = sorted(enumerate(items), key=lambda pair: _rank_key(pair[1], pair[0]), reverse=True)
    kept: list[tuple[int, dict[str, Any]]] = []
    dropped: list[str] = []
    used = 0
    for index, raw in ordered:
        item = dict(raw)
        cost = _token_count(item)
        identifier = str(item.get("id", f"E{index + 1}"))
        if cost <= budget - used:
            kept.append((index, item))
            used += cost
        else:
            dropped.append(identifier)
    kept.sort(key=lambda pair: pair[0])
    return [item for _, item in kept], dropped, used


def _scheduling_window(decision: FusionDecision) -> str:
    if decision.band == "NODATA":
        return "After the data-integrity issue is resolved"
    if decision.autonomy == "A0":
        return "After planner approval"
    if decision.autonomy == "A1":
        return "After technician acknowledgement"
    return "Next planned maintenance window"


def _citation_ids_from_text(value: Any) -> list[str]:
    return list(dict.fromkeys(match.group("id").upper() for match in _CITATION_RE.finditer(str(value))))


def _context_citation_ids(label: str, valid_ids: set[str]) -> list[str]:
    prefix = "M" if label.lower() == "history_result" else "S" if label.lower() == "sensor_result" else ""
    values = sorted(valid_ids, key=lambda value: (value[0], int(value[1:])))
    return [value for value in values if not prefix or value.startswith(prefix)]


def _normalise_text_citations(value: Any, valid_ids: set[str]) -> str:
    text = _GROUPED_CITATION_RE.sub(
        lambda match: "".join(f"[{identifier.upper()}]" for identifier in re.findall(r"[SM]\d+", match.group(1), re.IGNORECASE)),
        str(value),
    )
    return _CONTEXT_CITATION_RE.sub(
        lambda match: "".join(f"[{identifier}]" for identifier in _context_citation_ids(match.group(1), valid_ids)),
        text,
    )


def _normalise_citation_values(values: Sequence[Any], valid_ids: set[str]) -> list[str | None]:
    result: list[str | None] = []
    for value in values:
        identifier = _normalise_id(value)
        if identifier is not None:
            result.append(identifier)
            continue
        label = str(value).strip().strip("[]").lower()
        if label in {"fixed_fusion", "sensor_result", "history_result"}:
            result.extend(_context_citation_ids(label, valid_ids))
        else:
            result.append(None)
    return list(dict.fromkeys(result))


def _available_ids(items: Sequence[Mapping[str, Any]]) -> set[str]:
    return {identifier for item in items if (identifier := _normalise_id(item.get("id"))) is not None}


def _context_for_prompt(
    *,
    sensor: SensorEvidence,
    history: HistoryEvidence,
    machine: MachineContext,
    decision: FusionDecision,
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "sensor_result": {
            "p_failure_24h": sensor.p_failure_24h,
            "rul_p50_days": sensor.rul_p50_days,
            "predicted_mode": sensor.predicted_mode,
            "data_quality": sensor.data_quality,
            "evidence_count": sensor.evidence_count,
        },
        "history_result": {
            "history_prior": history.history_prior,
            "dominant_mode": history.dominant_mode,
            "repeat_failure": history.repeat_failure,
            "repeat_failure_count": history.repeat_failure_count,
        },
        "machine_meta": {
            "criticality": machine.criticality,
            "hourly_downtime_cost_inr": gbp_to_inr(machine.hourly_downtime_cost_gbp),
        },
        "fixed_fusion": {
            "risk": decision.risk,
            "band": decision.band,
            "confidence": decision.confidence,
            "autonomy": decision.autonomy,
            "action_class": decision.action_class,
            "scheduling_window": _scheduling_window(decision),
            "agreement": decision.agreement,
            "hard_rules": list(decision.hard_rules),
        },
        "evidence": list(evidence),
    }


def _ground_reasoning_step(value: Any, valid_ids: set[str]) -> str:
    """Attach resolvable provenance when Gemini omits trailing tags.

    The model is not allowed to invent an identifier here: tags are selected
    only from evidence already present in the bounded prompt.  Numeric and
    semantic claims still pass through the deterministic guardrail afterward.
    """

    text = _normalise_text_citations(value, valid_ids).strip()
    inline = _citation_ids_from_text(text)
    if _CITATION_SUFFIX_RE.search(text):
        return text
    lower = text.lower()
    sensor_ids = _context_citation_ids("sensor_result", valid_ids)
    history_ids = _context_citation_ids("history_result", valid_ids)
    selected = list(inline)
    if any(token in lower for token in ("sensor", "vibration", "temperature", "rul", "failure mode")):
        selected.extend(sensor_ids)
    if any(token in lower for token in ("history", "maintenance", "repair", "repeat", "lubric")):
        selected.extend(history_ids)
    if any(token in lower for token in ("fusion", "risk", "conflict", "agreement")):
        selected.extend([*sensor_ids[:2], *history_ids[:2]])
    selected = list(dict.fromkeys(selected)) or [*sensor_ids[:2], *history_ids[:2]]
    selected = [identifier for identifier in selected if identifier in valid_ids]
    if not selected:
        raise ValueError("reasoning step cannot be grounded because no citation evidence is available")
    punctuation = text[-1] if text.endswith((".", "!", "?")) else ""
    body = text[:-1].rstrip() if punctuation else text
    return f"{body} {''.join(f'[{identifier}]' for identifier in selected)}{punctuation}"


def _prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        # Packaging the module without its prompt must not make the offline
        # path fail; this is also useful in minimal test distributions.
        return "Return only the strict Coordinator JSON object described in the user evidence."


def _raw_draft(raw: Mapping[str, Any], valid_ids: set[str]) -> CoordinatorDraft:
    if set(raw) - _CORE_FIELDS:
        raise ValueError("LLM response contains fields outside the Coordinator schema")
    data = dict(raw)
    data["fault_diagnosis"] = _normalise_text_citations(data.get("fault_diagnosis", ""), valid_ids)
    for field_name in ("reasoning_steps", "facts", "assumptions"):
        values = data.get(field_name, [])
        if isinstance(values, list):
            data[field_name] = [
                _ground_reasoning_step(value, valid_ids)
                if field_name == "reasoning_steps"
                else _normalise_text_citations(value, valid_ids)
                for value in values
            ]
    data["limits"] = _normalise_text_citations(data.get("limits", ""), valid_ids)
    actions = data.get("recommended_actions")
    if not isinstance(actions, list):
        raise ValueError("recommended_actions must be a list")
    normalised_actions: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, Mapping):
            raise ValueError("recommended_actions must contain objects")
        item = dict(action)
        item["rationale"] = _normalise_text_citations(item.get("rationale", ""), valid_ids)
        item.setdefault("parts", [])
        inline = _citation_ids_from_text(item.get("rationale", ""))
        supplied = item.get("citations", [])
        if supplied is None:
            supplied = []
        if not isinstance(supplied, list):
            raise ValueError("action citations must be a list")
        item["citations"] = list(dict.fromkeys([*_normalise_citation_values(supplied, valid_ids), *inline]))
        if any(value is None for value in item["citations"]):
            raise ValueError("action contains malformed citation")
        normalised_actions.append(item)
    data["recommended_actions"] = normalised_actions
    citations = data.get("citations", [])
    if not isinstance(citations, list):
        raise ValueError("citations must be a list")
    all_inline = []
    for value in (*data.get("reasoning_steps", []), *data.get("facts", []), *data.get("recommended_actions", [])):
        if isinstance(value, Mapping):
            all_inline.extend(_citation_ids_from_text(value.get("rationale", "")))
        else:
            all_inline.extend(_citation_ids_from_text(value))
    data["citations"] = list(dict.fromkeys([*_normalise_citation_values(citations, valid_ids), *all_inline]))
    if any(value is None for value in data["citations"]):
        raise ValueError("response contains malformed citation")
    draft = CoordinatorDraft.model_validate(data)
    references = set(draft.citations)
    for action in draft.recommended_actions:
        references.update(action.citations)
        if not _citation_ids_from_text(action.rationale):
            raise ValueError("every action rationale must contain a citation")
        if not set(action.citations).issubset(valid_ids):
            raise ValueError("recommended action contains an unresolved citation")
    if not references.issubset(valid_ids):
        raise ValueError("response contains an unresolved citation")
    if any(not _citation_ids_from_text(fact) for fact in draft.facts):
        raise ValueError("facts must be traceable to citation tags")
    return draft


def _template(decision: FusionDecision, citation: str) -> CoordinatorDraft:
    window = _scheduling_window(decision)
    if decision.action_class == "verify_instrumentation":
        action = "Verify the suspected instrumentation channel before maintenance."
    elif decision.action_class == "monitor":
        action = "Monitor the fused risk state and review it at the planned window."
    elif decision.action_class == "NO_PREDICTION":
        action = "Resolve the telemetry quality issue before scheduling maintenance."
    else:
        action = "Inspect the affected equipment and prepare a controlled repair."
    ref = f"[{citation}]"
    return CoordinatorDraft(
        fault_diagnosis=f"Deterministic fusion returned {decision.band} risk and selected {decision.action_class}. {ref}",
        reasoning_steps=[
            f"The deterministic fusion band is {decision.band}. {ref}",
            f"The fusion decision fixes the confidence before narrative generation. {ref}",
            f"The selected action class is {decision.action_class}; scheduling is {window}. {ref}",
        ],
        facts=[f"The fusion decision is fixed before the narrative layer runs. {ref}"],
        assumptions=["The supplied evidence remains representative until the next review."],
        recommended_actions=[
            CoordinatorAction(
                action=action,
                priority="high" if decision.autonomy in {"A0", "A1"} else "medium",
                rationale=f"Follow the fixed fusion action class and scheduling window. {ref}",
                parts=[],
                est_labour_hours=0.0,
                window=window,
                citations=[citation],
            )
        ],
        limits="This deterministic template cannot identify details beyond the supplied fusion decision.",
        citations=[citation],
    )


class CoordinatorAgent:
    """Synthesize specialist evidence into a strict, grounded diagnosis."""

    name = "coordinator"
    version = PROMPT_VERSION
    model_name = "deterministic-template"
    provider = "local"

    def __init__(
        self,
        *,
        llm_client: Any | None = None,
        llm_api_key: str | None = None,
        llm_model_cfg: Any | None = None,
        prompt_version: str = PROMPT_VERSION,
        max_context_tokens: int | None = None,
    ) -> None:
        if prompt_version != PROMPT_VERSION:
            raise ValueError(f"unsupported Coordinator prompt version: {prompt_version}")
        self.prompt_version = prompt_version
        self.llm_api_key = (
            "injected-client" if llm_client is not None and llm_api_key is None else (settings.llm.api_key if llm_api_key is None else llm_api_key)
        )
        self.llm_client = llm_client or (create_client() if self.llm_api_key else None)
        self.llm_model_cfg = llm_model_cfg or settings.llm.models.coordinator
        self.max_context_tokens = int(max_context_tokens or settings.llm.max_context_tokens)
        if self.max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")

    def _call_llm(self, context: Mapping[str, Any]) -> tuple[CoordinatorDraft | None, LLMResponse | None, str | None]:
        if self.llm_client is None or not self.llm_api_key:
            return None, None, None
        messages = [
            {"role": "system", "content": _prompt()},
            {"role": "user", "content": json.dumps(_json_ready(context), sort_keys=True, separators=(",", ":"))},
        ]
        try:
            response = self.llm_client.chat(messages, self.llm_model_cfg)
            if isinstance(response, Mapping):
                response = LLMResponse(
                    text=str(response.get("text", response.get("content", ""))),
                    prompt_tokens=int(response.get("prompt_tokens", 0)),
                    completion_tokens=int(response.get("completion_tokens", 0)),
                    model=str(response.get("model", "")),
                    latency_ms=float(response.get("latency_ms", 0.0)),
                    cached=bool(response.get("cached", False)),
                    provider=str(response.get("provider", "")),
                )
            if not hasattr(response, "text"):
                raise TypeError("LLM client returned no text response")
            raw = parse_json(str(response.text))
            if not isinstance(raw, Mapping):
                raise ValueError("LLM response did not contain a JSON object")
            valid_ids = _available_ids(context.get("evidence", []))
            return _raw_draft(raw, valid_ids), response, None
        except Exception as exc:
            logger.warning("coordinator_llm_fallback error=%s", type(exc).__name__)
            return None, None, type(exc).__name__

    def run(self, ctx: RunContext) -> AgentResult:
        started = time.perf_counter()
        payload = ctx.payload
        sensor_value = payload.get("sensor_result", payload.get("sensor_evidence", payload))
        history_value = payload.get("history_result", payload.get("history_evidence", {}))
        machine_value = payload.get("machine_meta", payload.get("machine_context", {}))
        sensor = _sensor_input(sensor_value)
        history = _history_input(history_value)
        machine = _machine_input(machine_value)

        # This must remain the first decision-making call in the coordinator.
        # It is intentionally before prompt construction and before any LLM
        # call, so the narrative agent cannot set risk or autonomy.
        decision = fuse(sensor, history, machine)

        evidence = _evidence_items(payload, sensor_value, history_value)
        if not _available_ids(evidence):
            evidence = [{"id": "S1", "kind": "fusion", "detail": "Deterministic fusion decision"}, *evidence]
        kept, dropped, evidence_tokens = _trim_evidence(evidence, self.max_context_tokens)
        if not _available_ids(kept):
            # An oversized or non-citable bundle still needs one resolvable
            # reference for the complete offline contract.  This synthetic
            # item is explicitly fusion provenance, not a sensor claim.
            kept.insert(0, {"id": "S1", "kind": "fusion", "detail": "Deterministic fusion decision"})
            evidence_tokens = _token_count(kept)
        context = _context_for_prompt(sensor=sensor, history=history, machine=machine, decision=decision, evidence=kept)
        draft, response, llm_error = self._call_llm(context)
        fallback = draft is None
        citation = next(
            (identifier for item in kept if (identifier := _normalise_id(item.get("id"))) is not None),
            "S1",
        )
        if draft is None:
            draft = _template(decision, citation)

        window = _scheduling_window(decision)
        actions = [action.model_dump() for action in draft.recommended_actions]
        # The model can narrate an action but cannot choose its timing or
        # priority route.  Scheduling is a fusion output, so it is reapplied
        # after parsing the response.
        for action in actions:
            action["window"] = window
        model = "deterministic-template"
        provider = "local"
        prompt_version = self.prompt_version
        generated_at = datetime.now(UTC)
        prompt_tokens = 0
        completion_tokens = 0
        if not fallback and response is not None:
            model = str(getattr(response, "model", "") or _field(self.llm_model_cfg, "name", "configured"))
            provider = str(getattr(response, "provider", "") or getattr(self.llm_client, "provider", "llm"))
            prompt_tokens = int(getattr(response, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(response, "completion_tokens", 0) or 0)

        output_data = {
            **draft.model_dump(),
            "recommended_actions": actions,
            "risk": decision.risk,
            "band": decision.band,
            "confidence": decision.confidence,
            "autonomy": decision.autonomy,
            "action_class": decision.action_class,
            "scheduling_window": window,
            "fusion_terms": explain(decision),
            "generated": not fallback,
            "model": model,
            "provider": provider,
            "prompt_version": prompt_version,
            "generated_at": generated_at.isoformat(),
            "dropped_evidence": dropped,
            "evidence_tokens": evidence_tokens,
        }
        validated = CoordinatorOutput.model_validate(output_data)
        errors = ["llm_output_fallback"] if fallback and llm_error else []
        return AgentResult(
            ok=True,
            output=validated.model_dump(mode="json"),
            evidence=kept,
            confidence=decision.confidence,
            tokens={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            latency_ms=(time.perf_counter() - started) * 1000,
            errors=errors,
            fallback_used=fallback,
            model_name=model,
            provider=provider,
        )


__all__ = [
    "CoordinatorAction",
    "CoordinatorAgent",
    "CoordinatorDraft",
    "CoordinatorModel",
    "CoordinatorOutput",
    "PROMPT_VERSION",
]
