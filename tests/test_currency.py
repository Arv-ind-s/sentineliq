from __future__ import annotations

from decimal import Decimal

import pytest

from core.currency import GBP_TO_INR_RATE, gbp_to_inr, gbp_to_inr_whole


def test_gbp_amounts_are_converted_with_the_frozen_rate() -> None:
    assert GBP_TO_INR_RATE == Decimal("127.6005")
    assert gbp_to_inr(1) == 127.60
    assert gbp_to_inr(1_800) == 229_680.90
    assert gbp_to_inr_whole(385_058) == 49_133_593


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_currency_conversion_rejects_invalid_amounts(value: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        gbp_to_inr(value)


def test_currency_conversion_rejects_negative_precision() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        gbp_to_inr(1, decimal_places=-1)
