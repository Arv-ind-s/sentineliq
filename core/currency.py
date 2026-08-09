"""Currency conversion used at SentinelPdM's external boundaries.

The seeded operational data is historically denominated in GBP.  Renaming or
rewriting those persisted columns would make existing databases ambiguous, so
the application converts monetary values as they leave the domain layer.
Keeping the rate here makes that conversion deterministic and auditable.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import TypeAlias


MoneyValue: TypeAlias = int | float | Decimal

DISPLAY_CURRENCY = "INR"
GBP_TO_INR_RATE = Decimal("127.6005")
GBP_TO_INR_RATE_DATE = "2026-07-27"
GBP_TO_INR_RATE_SOURCE = "Bank of England series XUDLBK97"


def _converted(value: MoneyValue) -> Decimal:
    """Convert a non-negative historical GBP amount to INR without float drift."""

    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("currency amount must be finite and non-negative")
    return amount * GBP_TO_INR_RATE


def gbp_to_inr(value: MoneyValue, *, decimal_places: int = 2) -> float:
    """Return INR rounded to ``decimal_places`` using financial half-up rounding."""

    if decimal_places < 0:
        raise ValueError("decimal_places must be non-negative")
    quantum = Decimal(1).scaleb(-decimal_places)
    return float(_converted(value).quantize(quantum, rounding=ROUND_HALF_UP))


def gbp_to_inr_whole(value: MoneyValue) -> int:
    """Return whole rupees using financial half-up rounding."""

    return int(_converted(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def inr_to_gbp(value: MoneyValue, *, decimal_places: int = 2) -> float:
    """Convert a public INR amount to the legacy GBP persistence currency."""

    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("currency amount must be finite and non-negative")
    if decimal_places < 0:
        raise ValueError("decimal_places must be non-negative")
    quantum = Decimal(1).scaleb(-decimal_places)
    return float((amount / GBP_TO_INR_RATE).quantize(quantum, rounding=ROUND_HALF_UP))
