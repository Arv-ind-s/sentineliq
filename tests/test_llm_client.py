from __future__ import annotations

import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from core.config import settings
from llm.cache import LLMCache
from llm.client import (
    GeminiLLMClient,
    _call_json,
    parse_json,
)
from llm.embeddings import TfidfEmbeddingProvider


class FakeHTTP:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[tuple[tuple[object, ...], dict[str, object]]] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def post(self, *args: object, **kwargs: object) -> object:
        self.requests.append((args, kwargs))
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, BaseException):
            raise response
        return response


def _config(tmp_path: Path, **updates: object):
    breaker = settings.llm.circuit_breaker.model_copy(update={"failures": 4, "cooldown_s": 60})
    values = {
        "provider": "gemini",
        "base_url": "http://gemini.test/v1beta",
        "api_key": "test-key",
        "max_retries": 1,
        "circuit_breaker": breaker,
    }
    values.update(updates)
    return settings.llm.model_copy(update=values)


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://gemini.test/v1beta/models/model:generateContent")
    return httpx.Response(status_code, json=payload, request=request)


def test_parse_json_handles_fences_preamble_and_trailing_prose() -> None:
    expected = {"status": "ok", "nested": {"value": 3}}
    assert parse_json(f"```json\n{expected!r}\n```") is None
    valid = '{"status":"ok","nested":{"value":3}}'
    assert parse_json(f"```json\n{valid}\n```") == expected
    assert parse_json(f"Here is the diagnosis:\n{valid}") == expected
    assert parse_json(f"{valid}\nThis is explanatory prose.") == expected
    assert parse_json("not JSON") is None


def test_missing_key_fails_closed_without_network(tmp_path: Path) -> None:
    config = _config(tmp_path, api_key="")
    client = GeminiLLMClient(config, http_client=FakeHTTP([]), cache_path=tmp_path / "cache.db")
    started = time.perf_counter()
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) is None
    assert (time.perf_counter() - started) < 0.01
    client.close()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timeout"), _response({"error": "server"}, 500)])
def test_timeout_and_500_retry_then_fail_closed(tmp_path: Path, failure: object) -> None:
    fake = FakeHTTP([failure])
    client = GeminiLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) is None
    assert fake.calls == 2
    assert client.run_records[-1].success is False
    client.close()


def test_response_format_4xx_drops_parameter_for_session(tmp_path: Path) -> None:
    fake = FakeHTTP([
        _response({"error": {"message": "responseMimeType is unsupported"}}, 400),
        _response({"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}], "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1}}),
    ])
    client = GeminiLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) == {"ok": True}
    assert client._json_mode_dropped is True
    assert fake.calls == 2
    client.close()


def test_cache_hit_is_fast_and_records_usage(tmp_path: Path) -> None:
    fake = FakeHTTP([_response({"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}], "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2}})])
    client = GeminiLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
    messages = [{"role": "user", "content": "same evidence"}]
    first = client.chat(messages, {"name": "model", "temperature": 0.0, "max_tokens": 10})
    started = time.perf_counter()
    second = client.chat(messages, {"name": "model", "temperature": 0.0, "max_tokens": 10})
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert first.cached is False
    assert second.cached is True
    assert elapsed_ms <= 10
    assert fake.calls == 1
    assert client.run_records[-1].prompt_tokens == 4
    assert client.run_records[-1].provider == "gemini"
    args, kwargs = fake.requests[0]
    assert str(args[0]).endswith("/models/model:generateContent")
    assert kwargs["headers"] == {"x-goog-api-key": "test-key", "Content-Type": "application/json"}
    assert kwargs["json"] == {
        "contents": [{"role": "user", "parts": [{"text": "same evidence"}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10, "responseMimeType": "application/json"},
    }
    client.close()


def test_five_failures_open_breaker_and_suppress_network(tmp_path: Path) -> None:
    fake = FakeHTTP([_response({"error": "server"}, 500)])
    client = GeminiLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
    for _ in range(4):
        assert _call_json(client, [{"role": "user", "content": str(time.time_ns())}], {"name": "model"}) is None
    calls_after_open = fake.calls
    assert _call_json(client, [{"role": "user", "content": "fifth"}], {"name": "model"}) is None
    assert fake.calls == calls_after_open
    assert client.breaker_open is True
    client.close()


def test_tls_warning_is_emitted_once_and_health_contract_exposes_posture(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config = _config(tmp_path, tls_verify=False)
    with caplog.at_level("WARNING"):
        client = GeminiLLMClient(config, http_client=FakeHTTP([]), cache_path=tmp_path / "cache.db")
    assert client.tls_verified is False
    assert sum("tls_verify=false host=gemini.test" in message for message in caplog.messages) == 1
    client.close()


def test_system_prompt_and_mapping_content_use_native_gemini_shape(tmp_path: Path) -> None:
    fake = FakeHTTP([_response({"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]})])
    client = GeminiLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")

    assert _call_json(
        client,
        [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": {"evidence": ["M1"]}}],
        {"name": "model", "temperature": 0.1, "max_tokens": 20},
    ) == {"ok": True}

    payload = fake.requests[0][1]["json"]
    assert payload["system_instruction"] == {"parts": [{"text": "Return JSON."}]}
    assert payload["contents"] == [{"role": "user", "parts": [{"text": '{"evidence":["M1"]}'}]}]
    client.close()


def test_tfidf_provider_is_deterministic_and_offline() -> None:
    provider = TfidfEmbeddingProvider(max_features=20)
    matrix = provider.fit_transform(["bearing vibration", "pressure sensor", "bearing temperature"])
    assert matrix.dtype == np.float32
    assert matrix.shape[0] == 3
    assert provider.embed(["bearing vibration"]).shape[1] == matrix.shape[1]
