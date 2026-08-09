"""Privacy and prompt-injection controls for untrusted ERP text.

ERP notes are free text and therefore must be treated as both sensitive input
and adversarial input.  This module is intentionally dependency-light so it
can be used at ingestion, retrieval, caching, and prompt construction
boundaries.  Redaction replaces detected identifiers with typed markers; it
does not retain the original value for diagnostics.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass


_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w)"
)
_UK_PHONE_RE = re.compile(r"(?<!\w)(?:\+44|0)[\s.-]?(?:\d[\s.-]?){9,10}\d(?!\w)")
_EMPLOYEE_LABEL_RE = re.compile(
    r"(?i)\b(?:employee(?:\s+id)?|emp(?:loyee)?|eid)[\s:#-]*[A-Z]{0,4}[-_ ]?[A-Z0-9]*\d[A-Z0-9-]{2,}\b"
)
_EMPLOYEE_TOKEN_RE = re.compile(r"(?i)\bEMP[-_ ]?[A-Z0-9]*\d[A-Z0-9-]{2,}\b")
_SHORT_EMPLOYEE_TOKEN_RE = re.compile(r"(?i)\bE[-_]\d{3,}\b")
_NAME_LABEL_RE = re.compile(
    r"(?i)\b(?:full\s+name|name|operator|technician|engineer|reported\s+by|contact|supervisor)\s*[:=-]\s*"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}"
)
_NAME_CONTEXT_RE = re.compile(
    r"\b(?:(?i:operator|technician|engineer|supervisor|contact))"
    r"(?:\s+(?i:name|is))?\s*[:=-]?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"
)
_TITLED_NAME_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Eng)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"
)

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous",
        re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions?|directions?|messages?)\b"),
    ),
    ("system_prefix", re.compile(r"(?i)(?:^|\b)system\s*:")),
    ("identity_override", re.compile(r"(?i)\byou\s+are\s+now\b")),
    (
        "tool_call_syntax",
        re.compile(r"(?is)(?:\{\s*[\"'](?:tool|function|tool_call)[\"']\s*:|(?:tool_call|function_call)\s*[(:])"),
    ),
    ("long_base64", re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}(?![A-Za-z0-9+/])")),
)


@dataclass(frozen=True, slots=True)
class InjectionScanResult:
    """Stable result of scanning one free-text value."""

    matched_patterns: tuple[str, ...] = ()

    @property
    def detected(self) -> bool:
        return bool(self.matched_patterns)

    @property
    def patterns(self) -> tuple[str, ...]:
        """Compatibility alias used by callers that call them scan patterns."""

        return self.matched_patterns

    @property
    def matches(self) -> tuple[str, ...]:
        return self.matched_patterns

    def __bool__(self) -> bool:
        return self.detected


def _replace(pattern: re.Pattern[str], value: str, marker: str) -> str:
    return pattern.sub(marker, value)


def redact(text: str) -> str:
    """Replace names, emails, employee IDs, and phone numbers in free text.

    The expressions intentionally target labelled human identifiers and
    conventional contact formats.  Bare maintenance numbers are left alone
    so costs, dates, and measurements remain useful evidence.
    """

    value = "" if text is None else str(text)
    value = _replace(_EMAIL_RE, value, "[REDACTED_EMAIL]")
    value = _replace(_UK_PHONE_RE, value, "[REDACTED_PHONE]")
    value = _replace(_PHONE_RE, value, "[REDACTED_PHONE]")
    value = _replace(_EMPLOYEE_LABEL_RE, value, "[REDACTED_EMPLOYEE_ID]")
    value = _replace(_EMPLOYEE_TOKEN_RE, value, "[REDACTED_EMPLOYEE_ID]")
    value = _replace(_SHORT_EMPLOYEE_TOKEN_RE, value, "[REDACTED_EMPLOYEE_ID]")
    value = _replace(_NAME_LABEL_RE, value, "[REDACTED_NAME]")
    value = _replace(_NAME_CONTEXT_RE, value, "[REDACTED_NAME]")
    value = _replace(_TITLED_NAME_RE, value, "[REDACTED_NAME]")
    return value


def injection_scan(text: str) -> InjectionScanResult:
    """Detect instruction-shaped content before it can enter a prompt."""

    value = "" if text is None else str(text)
    matches = tuple(name for name, pattern in _INJECTION_PATTERNS if pattern.search(value))
    return InjectionScanResult(matches)


def wrap_untrusted_document(text: str, document_id: str) -> str:
    """Wrap sanitized ERP text as data and escape delimiter-breaking markup."""

    identifier = str(document_id).strip().strip("[]")
    if not re.fullmatch(r"M\d+", identifier, flags=re.IGNORECASE):
        raise ValueError("document_id must be an M# citation identifier")
    safe_text = html.escape("" if text is None else str(text), quote=False)
    return f'<untrusted_document id="{identifier.upper()}">{safe_text}</untrusted_document>'


# A descriptive alias makes the boundary obvious to integrations that prefer
# verb-based naming while keeping the task's public ``injection_scan`` name.
scan_injection = injection_scan


__all__ = ["InjectionScanResult", "injection_scan", "redact", "scan_injection", "wrap_untrusted_document"]
