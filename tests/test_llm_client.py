from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from core.config import settings
from llm.cache import LLMCache
from llm.client import (
    GatewayHTTPError,
    HttpxLLMClient,
    LangChainLLMClient,
    _call_json,
    parse_json,
)
from llm.embeddings import TfidfEmbeddingProvider


class FakeHTTP:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    def post(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, BaseException):
            raise response
        return response


def _config(tmp_path: Path, **updates: object):
    breaker = settings.llm.circuit_breaker.model_copy(update={"failures": 4, "cooldown_s": 60})
    values = {
        "provider": "httpx",
        "base_url": "http://gateway.test",
        "api_key": "test-key",
        "max_retries": 1,
        "circuit_breaker": breaker,
    }
    values.update(updates)
    return settings.llm.model_copy(update=values)


def _response(payload: object, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://gateway.test/chat/completions")
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
    client = HttpxLLMClient(config, http_client=FakeHTTP([]), cache_path=tmp_path / "cache.db")
    started = time.perf_counter()
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) is None
    assert (time.perf_counter() - started) < 0.01
    client.close()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timeout"), _response({"error": "server"}, 500)])
def test_timeout_and_500_retry_then_fail_closed(tmp_path: Path, failure: object) -> None:
    fake = FakeHTTP([failure])
    client = HttpxLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) is None
    assert fake.calls == 2
    assert client.run_records[-1].success is False
    client.close()


def test_response_format_4xx_drops_parameter_for_session(tmp_path: Path) -> None:
    fake = FakeHTTP([
        _response({"error": {"message": "response_format is unsupported"}}, 400),
        _response({"choices": [{"message": {"content": '{"ok":true}'}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}),
    ])
    client = HttpxLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) == {"ok": True}
    assert client._json_mode_dropped is True
    assert fake.calls == 2
    client.close()


def test_cache_hit_is_fast_and_records_usage(tmp_path: Path) -> None:
    fake = FakeHTTP([_response({"choices": [{"message": {"content": '{"ok":true}'}}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}})])
    client = HttpxLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
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
    assert client.run_records[-1].provider == "httpx"
    client.close()


def test_five_failures_open_breaker_and_suppress_network(tmp_path: Path) -> None:
    fake = FakeHTTP([_response({"error": "server"}, 500)])
    client = HttpxLLMClient(_config(tmp_path), http_client=fake, cache_path=tmp_path / "cache.db")
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
        client = HttpxLLMClient(config, http_client=FakeHTTP([]), cache_path=tmp_path / "cache.db")
    assert client.tls_verified is False
    assert sum("tls_verify=false host=gateway.test" in message for message in caplog.messages) == 1
    client.close()


def test_langchain_import_failure_does_not_escape_call_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    client = LangChainLLMClient(_config(tmp_path), cache_path=tmp_path / "cache.db")
    assert _call_json(client, [{"role": "user", "content": "hello"}], {"name": "model"}) is None
    client.close()


def test_tfidf_provider_is_deterministic_and_offline() -> None:
    provider = TfidfEmbeddingProvider(max_features=20)
    matrix = provider.fit_transform(["bearing vibration", "pressure sensor", "bearing temperature"])
    assert matrix.dtype == np.float32
    assert matrix.shape[0] == 3
    assert provider.embed(["bearing vibration"]).shape[1] == matrix.shape[1]
