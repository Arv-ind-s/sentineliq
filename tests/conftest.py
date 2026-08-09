from __future__ import annotations

import pytest

from core.config import settings


@pytest.fixture(autouse=True)
def disable_external_llm_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the test suite deterministic and free of external API calls.

    Tests that exercise the generated path inject a local protocol-compatible
    client and explicitly set a non-empty key for that test only.
    """

    monkeypatch.setattr(settings.llm, "api_key", "")
