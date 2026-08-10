from __future__ import annotations

import asyncio
import ssl
from typing import Any

import api.main as api_main


class CatalogResponse:
    status_code = 200

    def __init__(self, model_ids: list[str]) -> None:
        self._model_ids = model_ids

    def json(self) -> dict[str, list[dict[str, str]]]:
        return {
            "models": [
                {"name": f"models/{model_id}", "supportedGenerationMethods": ["generateContent"]}
                for model_id in self._model_ids
            ]
        }


class CatalogClient:
    last_verify: Any = None
    model_ids: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_verify = kwargs.get("verify")

    async def __aenter__(self) -> "CatalogClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> CatalogResponse:
        assert url.endswith("/models")
        assert kwargs["headers"] == {"x-goog-api-key": "test-key"}
        return CatalogResponse(type(self).model_ids)


def test_llm_health_requires_the_configured_model_in_the_gemini_catalog(monkeypatch) -> None:
    monkeypatch.setattr(api_main.settings.llm, "api_key", "test-key")
    monkeypatch.setattr(api_main.httpx, "AsyncClient", CatalogClient)
    CatalogClient.model_ids = [api_main.settings.llm.models.coordinator.name]

    status, _, detail, _, reachable, tls_verified, _, _ = asyncio.run(api_main._check_llm())

    assert status == "up"
    assert reachable is True
    assert tls_verified is True
    assert "model available" in detail
    assert isinstance(CatalogClient.last_verify, ssl.SSLContext)


def test_llm_health_degrades_when_the_configured_model_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(api_main.settings.llm, "api_key", "test-key")
    monkeypatch.setattr(api_main.httpx, "AsyncClient", CatalogClient)
    CatalogClient.model_ids = ["another-model"]

    status, _, detail, _, reachable, _, _, _ = asyncio.run(api_main._check_llm())

    assert status == "degraded"
    assert reachable is True
    assert "model unavailable" in detail
