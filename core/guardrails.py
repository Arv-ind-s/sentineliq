"""Deterministic validation of coordinator drafts before they reach a user.

This module intentionally has no LLM dependency.  It validates the coordinator
contract, grounds every material number against an evidence bundle, resolves
citations, blocks unsafe recommendations, and moves uncited facts into
assumptions.  Rejections are written to the existing audit ledger so they are
durable and queryable without adding a second security log or changing the
13-table core schema.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from core.audit import AuditEvent, append_events
from core.db import session_scope
from core.models import AuditLedger
from core.redaction import injection_scan, redact, wrap_untrusted_document


logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(?P<id>[SM]\d+)\]")
_ISO_DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?\b")
_IDENTIFIER_RE = re.compile(r"\b(?=[A-Za-z0-9_.:-]*[A-Za-z])(?=[A-Za-z0-9_.:-]*\d)[A-Za-z][A-Za-z0-9_.:-]*\b")
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<currency>[₹£$€])?\s*"
    r"(?P<number>(?:\d{1,3}(?:,\d{2})+,\d{3}|\d+(?:,\d{3})*)(?:\.\d+)?)\s*"
    r"(?P<scale>[kKmM])?\s*"
    r"(?P<unit>%|°\s*[CFcf]|[CFcf](?![A-Za-z])|mm\s*/\s*s|m\s*/\s*s|"
    r"days?|d|hours?|hrs?|h|minutes?|mins?|min|seconds?|secs?|s|"
    r"gbp|pounds?|inr|rupees?)?",
    re.IGNORECASE,
)


class GuardrailModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RecommendedAction(GuardrailModel):
    action: str = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    rationale: str = Field(min_length=1)
    parts: list[str]
    est_labour_hours: float = Field(ge=0)
    window: str = Field(min_length=1)
    citations: list[str] = Field(min_length=1)


class DiagnosisDraft(GuardrailModel):
    """Strict Coordinator output contract from spec §3.8.3."""

    fault_diagnosis: str = Field(min_length=1, max_length=500)
    reasoning_steps: list[str] = Field(min_length=1, max_length=5)
    facts: list[str]
    assumptions: list[str]
    recommended_actions: list[RecommendedAction] = Field(min_length=1, max_length=4)
    limits: str = Field(min_length=1)
    citations: list[str]


@dataclass(frozen=True)
class NormalisedQuantity:
    """A bundle quantity plus all legal representations of that quantity."""

    source: str
    dimension: str
    value: float
    representations: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ParsedNumber:
    raw: str
    value: float
    unit: str
    currency: str
    scale: str


@dataclass(frozen=True)
class GuardrailRejection:
    trace_id: str
    rule: str
    offending: str
    message: str
    outcome: str = "rejected"

    def as_dict(self) -> dict[str, str]:
        return {
            "trace_id": self.trace_id,
            "rule": self.rule,
            "offending": self.offending,
            "message": self.message,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class GuardrailResult:
    accepted: bool
    draft: DiagnosisDraft | None
    rejections: tuple[GuardrailRejection, ...] = ()
    autonomy: str = "A3"
    moved_facts: tuple[str, ...] = ()

    @property
    def rejected(self) -> bool:
        return not self.accepted

    @property
    def ok(self) -> bool:
        """Compatibility alias for callers that use the AgentResult convention."""

        return self.accepted

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "draft": self.draft.model_dump() if self.draft else None,
            "rejections": [item.as_dict() for item in self.rejections],
            "autonomy": self.autonomy,
            "moved_facts": list(self.moved_facts),
        }


def _dimension_for(source: str) -> str:
    key = source.lower()
    if any(token in key for token in ("confidence", "probability", "p_failure", "prob")):
        return "probability"
    if any(token in key for token in ("rul", "duration", "days", "hours", "minutes", "seconds")):
        return "duration"
    if any(token in key for token in ("temperature", "temp")):
        return "temperature"
    if any(token in key for token in ("cost", "gbp", "inr", "price", "pound", "rupee", "£", "₹")):
        return "cost"
    if "risk" in key:
        return "risk"
    return "generic"


def _bundle_representations(value: float, dimension: str) -> tuple[tuple[str, float], ...]:
    if dimension == "probability":
        return (("probability", value), ("probability", value * 100), ("generic", value))
    if dimension == "duration":
        return (("duration", value), ("duration", value * 24), ("duration", value * 24 * 60))
    if dimension == "temperature":
        return (("temperature", value), ("temperature", value * 9 / 5 + 32))
    if dimension == "cost":
        return (("cost", value), ("cost", value / 1000), ("generic", value))
    if dimension == "risk":
        return (("risk", value), ("risk", value / 100 if value > 1 else value * 100), ("generic", value))
    return (("generic", value),)


def _walk_numeric(value: Any, path: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, Mapping):
        # Evidence records commonly store a physical quantity as
        # ``{"channel": "temp_bearing_c", "value": 58.3}``.  A plain
        # recursive walk would classify that value from the generic key
        # ``value`` and lose its temperature dimension.  Carry the sibling
        # channel/unit metadata into the source path so unit-aware grounding
        # can compare the number correctly.
        qualifier = str(value.get("channel") or value.get("unit") or "").strip()
        for key, child in value.items():
            child_key = f"{qualifier}.{key}" if key == "value" and qualifier else str(key)
            child_path = f"{path}.{child_key}" if path else child_key
            yield from _walk_numeric(child, child_path)
        return
    if isinstance(value, (list, tuple, set)):
        for index, child in enumerate(value):
            yield from _walk_numeric(child, f"{path}[{index}]")
        return
    if isinstance(value, (int, float)):
        yield path, float(value)


def normalise_numeric(bundle: Mapping[str, Any] | BaseModel) -> tuple[NormalisedQuantity, ...]:
    """Expand evidence quantities into unit/scale representations.

    The returned values retain their source paths for diagnostics, while the
    representation tuples provide the comparison space used by grounding.
    """

    raw = bundle.model_dump() if isinstance(bundle, BaseModel) else bundle
    return tuple(
        NormalisedQuantity(
            source=source,
            dimension=_dimension_for(source),
            value=value,
            representations=_bundle_representations(value, _dimension_for(source)),
        )
        for source, value in _walk_numeric(raw)
    )


def _parse_numbers(text: str) -> tuple[ParsedNumber, ...]:
    parsed: list[ParsedNumber] = []
    for match in _NUMBER_RE.finditer(text):
        raw_value = match.group("number").replace(",", "")
        unit = re.sub(r"\s+", "", match.group("unit") or "").lower()
        parsed.append(
            ParsedNumber(
                raw=match.group(0).strip(),
                value=float(raw_value),
                unit=unit,
                currency=match.group("currency") or "",
                scale=(match.group("scale") or "").lower(),
            )
        )
    return tuple(parsed)


def _parsed_representations(number: ParsedNumber) -> tuple[tuple[str, float], ...]:
    value = number.value
    unit = number.unit
    if unit == "%":
        return (("probability", value / 100), ("risk", value))
    if unit in {"c", "°c"}:
        return (("temperature", value),)
    if unit in {"f", "°f"}:
        return (("temperature", (value - 32) * 5 / 9),)
    if unit in {"d", "day", "days"}:
        return (("duration", value),)
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return (("duration", value / 24),)
    if unit in {"min", "mins", "minute", "minutes"}:
        return (("duration", value / (24 * 60)),)
    if unit in {"s", "sec", "secs", "second", "seconds"}:
        return (("duration", value / (24 * 60 * 60)),)
    if unit in {"m/s", "mm/s"}:
        return (("generic", value),)
    if number.currency or unit in {"gbp", "pound", "pounds", "inr", "rupee", "rupees"} or number.scale:
        scaled = value * (1000 if number.scale == "k" else 1_000_000 if number.scale == "m" else 1)
        return (("cost", scaled),)
    candidates = [("generic", value)]
    if 0 <= value <= 1:
        candidates.append(("probability", value))
    if 1 < value <= 100:
        candidates.extend((("probability", value / 100), ("risk", value)))
    return tuple(candidates)


def _within_tolerance(candidate: float, expected: float, dimension: str) -> bool:
    tolerance = max(abs(expected) * 0.02, 1e-6)
    if abs(candidate - expected) <= tolerance:
        return True
    # Human-readable duration is commonly rounded from 4.2 d to 4 d.
    return dimension == "duration" and abs(candidate - expected) <= 0.25 and (candidate.is_integer() or expected.is_integer())


def numeric_grounding(draft: DiagnosisDraft, bundle: Mapping[str, Any] | BaseModel, *, trace_id: str = "") -> list[GuardrailRejection]:
    """Return numeric-grounding violations for a validated draft."""

    raw = bundle.model_dump() if isinstance(bundle, BaseModel) else bundle
    indexed: dict[str, Any] = {}

    def index(value: Any) -> None:
        if isinstance(value, Mapping):
            identifier = str(value.get("id", "")).strip("[]").upper()
            if re.fullmatch(r"[SM]\d+", identifier):
                indexed[identifier] = value
            for key, child in value.items():
                if re.fullmatch(r"[SM]\d+", str(key).upper()):
                    indexed[str(key).upper()] = child
                index(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                index(child)

    index(raw)
    violations: list[GuardrailRejection] = []
    # Only prose fields carry claims that must trace back to evidence. Structured
    # planning fields (est_labour_hours, priority) are the Coordinator's own
    # operational estimate, not a fact about the evidence bundle, so they are
    # deliberately excluded via `_draft_strings` (fractional hours like 0.5
    # would otherwise fail grounding and reject an otherwise-valid draft).
    claims = [draft.fault_diagnosis, *draft.reasoning_steps, *draft.facts, *draft.assumptions, draft.limits]
    for action in draft.recommended_actions:
        suffix = "".join(f"[{ref.strip('[]')}]" for ref in action.citations)
        claims.extend(f"{value} {suffix}" for value in (action.action, action.rationale, action.window, *action.parts))
    for claim in claims:
        refs = {match.group("id") for match in _CITATION_RE.finditer(claim)}
        support = {ref: indexed[ref] for ref in refs if ref in indexed}
        # Sensor/model aggregates are computed from the sensor evidence; they
        # can support derived risk and RUL claims when sensor evidence is cited.
        if any(ref.startswith("S") for ref in refs):
            for section in ("sensor", "fusion"):
                data = raw.get(section, {})
                if isinstance(data, Mapping):
                    support[section] = {key: value for key, value in data.items()
                                        if key != "evidence" and not re.fullmatch(r"[SM]\d+", str(key))}
        allowed = [representation for quantity in normalise_numeric(support) for representation in quantity.representations]
        prose = _IDENTIFIER_RE.sub(" ", _ISO_DATETIME_RE.sub(" ", _CITATION_RE.sub(" ", claim)))
        for number in _parse_numbers(prose):
            if any(
                dimension == candidate_dimension and _within_tolerance(value, expected, dimension)
                for candidate_dimension, value in _parsed_representations(number)
                for dimension, expected in allowed
            ):
                continue
            violations.append(GuardrailRejection(trace_id, "numeric_grounding", number.raw, f"Number {number.raw!r} is not supported by the cited evidence."))
    return violations


def _evidence_ids(bundle: Mapping[str, Any] | BaseModel) -> set[str]:
    raw = bundle.model_dump() if isinstance(bundle, BaseModel) else bundle
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            # Retrieval uses bracketed display labels (``[M1]``), while the
            # Coordinator and guardrail schemas use canonical IDs (``M1``).
            # Accept either representation at this trust boundary and keep
            # the canonical value internally.
            identifier = str(value.get("id", "")).strip().strip("[]").upper()
            if re.fullmatch(r"[SM]\d+", identifier):
                found.add(identifier)
            for key, child in value.items():
                key_text = str(key).upper()
                if re.fullmatch(r"[SM]\d+", key_text):
                    found.add(key_text)
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(raw)
    return found


def _draft_strings(draft: DiagnosisDraft) -> Iterable[str]:
    yield draft.fault_diagnosis
    yield from draft.reasoning_steps
    yield from draft.facts
    yield from draft.assumptions
    yield draft.limits
    yield from draft.citations
    for action in draft.recommended_actions:
        yield action.action
        yield action.rationale
        yield action.window
        yield from action.parts
        yield from action.citations


def citation_validation(draft: DiagnosisDraft, bundle: Mapping[str, Any] | BaseModel, *, trace_id: str = "") -> list[GuardrailRejection]:
    """Validate inline and structured citation references against evidence IDs."""

    valid_ids = _evidence_ids(bundle)
    references: list[str] = []
    for text in _draft_strings(draft):
        references.extend(match.group("id").upper() for match in _CITATION_RE.finditer(text))
    for action in draft.recommended_actions:
        references.extend(reference.strip("[]").upper() for reference in action.citations)
    references.extend(reference.strip("[]").upper() for reference in draft.citations)
    violations: list[GuardrailRejection] = []
    for claim in (draft.fault_diagnosis, *draft.reasoning_steps, *(action.rationale for action in draft.recommended_actions)):
        if not _CITATION_RE.search(claim):
            violations.append(GuardrailRejection(trace_id, "citation_validity", claim, "Diagnosis, reasoning, and action rationale require supporting citations."))
    for reference in references:
        if reference not in valid_ids:
            violations.append(GuardrailRejection(trace_id, "citation_validity", f"[{reference}]", f"Citation [{reference}] does not resolve to an evidence item."))
    return violations


def unsafe_action_validation(draft: DiagnosisDraft, *, trace_id: str = "") -> list[GuardrailRejection]:
    text = " ".join(_draft_strings(draft)).lower().replace("’", "'")
    violations: list[GuardrailRejection] = []
    patterns = (
        r"\b(?:bypass|disable|override|ignore|suppress|remove|disconnect|turn\s+off|switch\s+off)\b[^.;!?]{0,60}\b(?:interlocks?|alarms?|relief|guards?|emergency\s+stop|safety\s+shutdown)\b",
        r"\b(?:continue|keep)\s+(?:operating|running)\b[^.;!?]{0,90}\b(?:overheat\w*|critical|leak\w*)\b",
        r"\b(?:defer|delay|postpone)\s+(?:maintenance|repair\w*|inspection)\b[^.;!?]{0,90}\bcritical\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            prefix = text[max(0, match.start() - 30):match.start()]
            if re.search(r"\b(?:do\s+not|don't|never|must\s+not|mustn't)\s+(?:ever\s+)?$", prefix):
                continue
            violations.append(GuardrailRejection(trace_id, "unsafe_action", match.group(), "Unsafe recommendation disables protection or delays a critical intervention."))
    return violations


def _assumption_discipline(draft: DiagnosisDraft) -> tuple[DiagnosisDraft, tuple[str, ...]]:
    cited_facts: list[str] = []
    uncited: list[str] = []
    for fact in draft.facts:
        (cited_facts if _CITATION_RE.search(fact) else uncited).append(fact)
    if not uncited:
        return draft, ()
    return draft.model_copy(update={"facts": cited_facts, "assumptions": [*draft.assumptions, *uncited]}), tuple(uncited)


def _schema_rejection(error: ValidationError, trace_id: str) -> GuardrailRejection:
    first = error.errors()[0] if error.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ())) or "draft"
    return GuardrailRejection(trace_id, "schema", location, f"Draft failed strict schema validation: {first.get('msg', 'invalid draft')}.")


def _persist_rejections(rejections: Iterable[GuardrailRejection], *, database_url: str | None) -> None:
    values = list(rejections)
    if not values:
        return
    append_events(
        [
            AuditEvent(
                actor="guardrail",
                action="guardrail_rejection",
                subject=rejection.rule,
                payload=rejection.as_dict(),
            )
            for rejection in values
        ],
        database_url=database_url,
    )


def load_rejections(*, trace_id: str | None = None, database_url: str | None = None) -> list[GuardrailRejection]:
    """Return durable guardrail rejection rows for the Trust screen."""

    with session_scope(database_url) as session:
        query = select(AuditLedger).where(AuditLedger.action == "guardrail_rejection").order_by(AuditLedger.seq)
        rows = session.scalars(query).all()
    result = []
    for row in rows:
        value = dict(row.payload_json)
        if trace_id is None or value.get("trace_id") == trace_id:
            result.append(GuardrailRejection(trace_id=str(value.get("trace_id", "")), rule=str(value.get("rule", row.subject)), offending=str(value.get("offending", "")), message=str(value.get("message", "")), outcome=str(value.get("outcome", "rejected"))))
    return result


def run_guardrails(
    draft: DiagnosisDraft | Mapping[str, Any],
    evidence_bundle: Mapping[str, Any],
    *,
    trace_id: str = "guardrail",
    database_url: str | None = None,
) -> GuardrailResult:
    """Validate one Coordinator draft and persist every rejection."""

    rejections: list[GuardrailRejection] = []
    try:
        validated = draft if isinstance(draft, DiagnosisDraft) else DiagnosisDraft.model_validate(draft)
    except ValidationError as error:
        rejections.append(_schema_rejection(error, trace_id))
        _persist_rejections(rejections, database_url=database_url)
        logger.warning("guardrail_rejection trace_id=%s rule=%s offending=%s", trace_id, rejections[0].rule, rejections[0].offending)
        return GuardrailResult(False, None, tuple(rejections), autonomy="A0")

    rejections.extend(numeric_grounding(validated, evidence_bundle, trace_id=trace_id))
    rejections.extend(citation_validation(validated, evidence_bundle, trace_id=trace_id))
    rejections.extend(unsafe_action_validation(validated, trace_id=trace_id))
    autonomy = "A0" if any(item.rule == "unsafe_action" for item in rejections) else "A3"
    disciplined, moved_facts = _assumption_discipline(validated)
    _persist_rejections(rejections, database_url=database_url)
    for rejection in rejections:
        logger.warning("guardrail_rejection trace_id=%s rule=%s offending=%s", rejection.trace_id, rejection.rule, rejection.offending)
    return GuardrailResult(not rejections, disciplined, tuple(rejections), autonomy=autonomy, moved_facts=moved_facts)


def validate_draft(*args: Any, **kwargs: Any) -> GuardrailResult:
    """Discoverable alias for callers that prefer validation terminology."""

    return run_guardrails(*args, **kwargs)


class Guardrail:
    """Injectable object seam for the deterministic guardrail checks."""

    def __init__(self, *, database_url: str | None = None) -> None:
        self.database_url = database_url

    def check(self, draft: DiagnosisDraft | Mapping[str, Any], evidence_bundle: Mapping[str, Any], *, trace_id: str = "guardrail") -> GuardrailResult:
        return run_guardrails(draft, evidence_bundle, trace_id=trace_id, database_url=self.database_url)


__all__ = [
    "DiagnosisDraft",
    "Guardrail",
    "GuardrailModel",
    "GuardrailRejection",
    "GuardrailResult",
    "NormalisedQuantity",
    "RecommendedAction",
    "citation_validation",
    "load_rejections",
    "normalise_numeric",
    "numeric_grounding",
    "run_guardrails",
    "unsafe_action_validation",
    "validate_draft",
]
