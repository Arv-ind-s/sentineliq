"""Fail-closed client for the Gemini Developer API."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import quote, urlparse

import httpx

from core.config import ROOT, settings
from llm.cache import CachedResponse, LLMCache, cache_key, evidence_bundle
from llm.tls import GeminiVerifySetting, gemini_verify_setting


logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Base class for Gemini API failures."""


class MissingAPIKeyError(LLMError):
    pass


class CircuitOpenError(LLMError):
    pass


class UnsupportedResponseFormatError(LLMError):
    pass


class TransportError(LLMError):
    pass


class GeminiHTTPError(LLMError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: float
    cached: bool = False
    provider: str = ""


@dataclass(frozen=True)
class LLMRunRecord:
    created_at: datetime
    prompt_tokens: int
    completion_tokens: int
    model: str
    provider: str
    latency_ms: float
    cached: bool
    success: bool
    error: str | None = None


class LLMClient(Protocol):
    """Stable interface consumed by agents."""

    def chat(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> LLMResponse: ...


def _model_value(model_cfg: Any, field: str, default: Any = None) -> Any:
    if isinstance(model_cfg, Mapping):
        return model_cfg.get(field, default)
    return getattr(model_cfg, field, default)


def _token_count(value: Any) -> int:
    if not value:
        return 0
    if isinstance(value, str):
        return max(1, len(value.split()))
    return _token_count(json.dumps(value, sort_keys=True, default=str))


def parse_json(text: str | None) -> dict[str, Any] | None:
    """Extract the first balanced JSON object from model output."""

    if not text:
        return None
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1])
                    except (TypeError, json.JSONDecodeError):
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


def failure_reason(error: BaseException) -> str:
    """Map a Gemini exception to the smoke-test vocabulary."""

    if isinstance(error, MissingAPIKeyError):
        return "auth"
    if isinstance(error, GeminiHTTPError):
        if error.status_code in {401, 403}:
            return "auth"
        if error.status_code == 404:
            return "model_not_found"
    message = str(error).lower()
    if "ssl" in message or "tls" in message or "certificate" in message:
        return "tls"
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, TransportError, TimeoutError)):
        return "unreachable"
    if isinstance(error, (httpx.ConnectError, httpx.ReadError)):
        return "unreachable"
    return "unreachable"


class _CircuitBreaker:
    def __init__(self, failures: int, cooldown_s: int, clock: Any = time.monotonic) -> None:
        self.failures_limit = failures
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if float(self._clock()) - self._opened_at >= self.cooldown_s:
                self._opened_at = None
                self._failure_count = 0
                return False
            return True

    def before_call(self) -> None:
        if self.is_open:
            raise CircuitOpenError("LLM circuit breaker is open")

    def success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._opened_at = None

    def failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failures_limit:
                self._opened_at = float(self._clock())


class _GeminiBaseClient:
    provider = "gemini"

    def __init__(
        self,
        config: Any = None,
        *,
        cache: LLMCache | None = None,
        cache_path: str | Path | None = None,
        clock: Any = time.time,
        monotonic_clock: Any = time.monotonic,
        emit_tls_warning: bool = True,
        **overrides: Any,
    ) -> None:
        llm_config = getattr(config, "llm", config) if config is not None else settings.llm
        def value(name: str, fallback: Any) -> Any:
            if name in overrides:
                return overrides[name]
            if isinstance(llm_config, Mapping):
                return llm_config.get(name, fallback)
            return getattr(llm_config, name, fallback)

        self.base_url = str(value("base_url", settings.llm.base_url)).rstrip("/")
        self.api_key = str(value("api_key", settings.llm.api_key) or "")
        self.tls_verify = bool(value("tls_verify", settings.llm.tls_verify))
        self.timeout_s = float(value("timeout_s", settings.llm.timeout_s))
        self.max_retries = int(value("max_retries", settings.llm.max_retries))
        self.json_mode = str(value("json_mode", settings.llm.json_mode))
        self.prompt_version = str(value("prompt_version", settings.llm.prompt_version))
        breaker_config = llm_config.get("circuit_breaker", settings.llm.circuit_breaker) if isinstance(llm_config, Mapping) else getattr(llm_config, "circuit_breaker", settings.llm.circuit_breaker)
        breaker_failures = breaker_config.get("failures", settings.llm.circuit_breaker.failures) if isinstance(breaker_config, Mapping) else getattr(breaker_config, "failures", settings.llm.circuit_breaker.failures)
        breaker_cooldown = breaker_config.get("cooldown_s", settings.llm.circuit_breaker.cooldown_s) if isinstance(breaker_config, Mapping) else getattr(breaker_config, "cooldown_s", settings.llm.circuit_breaker.cooldown_s)
        self.breaker = _CircuitBreaker(int(breaker_failures), int(breaker_cooldown), monotonic_clock)
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._json_mode_dropped = self.json_mode == "prompt_only"
        self._runs: list[LLMRunRecord] = []
        self._run_lock = threading.Lock()
        if cache is not None:
            self.cache = cache
        else:
            configured_cache = llm_config.get("cache", settings.llm.cache) if isinstance(llm_config, Mapping) else getattr(llm_config, "cache", settings.llm.cache)
            if cache_path is None:
                cache_path = ROOT / "data" / "llm_cache.db"
            cache_ttl = configured_cache.get("ttl_s", settings.llm.cache.ttl_s) if isinstance(configured_cache, Mapping) else getattr(configured_cache, "ttl_s", settings.llm.cache.ttl_s)
            self.cache = LLMCache(cache_path, int(cache_ttl), clock)
        if emit_tls_warning:
            self._emit_tls_warning()

    @property
    def tls_verified(self) -> bool:
        return self.tls_verify

    @property
    def run_records(self) -> tuple[LLMRunRecord, ...]:
        with self._run_lock:
            return tuple(self._runs)

    @property
    def breaker_open(self) -> bool:
        return self.breaker.is_open

    def _emit_tls_warning(self) -> None:
        if not self.tls_verify:
            host = urlparse(self.base_url).hostname or self.base_url
            logger.warning("tls_verify=false host=%s", host)

    def _verify_setting(self) -> GeminiVerifySetting:
        return gemini_verify_setting(
            tls_verify=self.tls_verify,
            cert_file=getattr(settings, "ssl_cert_file", None),
        )

    def _append_run(self, record: LLMRunRecord) -> None:
        with self._run_lock:
            self._runs.append(record)
        self.cache.record_run(
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            model=record.model,
            provider=record.provider,
            latency_ms=record.latency_ms,
            cached=record.cached,
            success=record.success,
            error=record.error,
        )

    def _record(self, *, started: float, model: str, prompt_tokens: int, completion_tokens: int, cached: bool, success: bool, error: BaseException | None = None) -> None:
        latency_ms = max(0.0, (float(self._clock()) - started) * 1000.0)
        record = LLMRunRecord(datetime.now(UTC), prompt_tokens, completion_tokens, model, self.provider, latency_ms, cached, success, str(error) if error else None)
        self._append_run(record)

    def _cache_key(self, messages: Sequence[Mapping[str, Any]], model: str) -> str:
        return cache_key(self.prompt_version, model, evidence_bundle(messages))

    def _request_payload(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> dict[str, Any]:
        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user")).lower()
            raw_content = message.get("content", "")
            text = raw_content if isinstance(raw_content, str) else json.dumps(raw_content, sort_keys=True, default=str, separators=(",", ":"))
            if role == "system":
                system_parts.append({"text": text})
                continue
            gemini_role = "model" if role in {"assistant", "model"} else "user"
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"].append({"text": text})
            else:
                contents.append({"role": gemini_role, "parts": [{"text": text}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": _model_value(model_cfg, "temperature", 0.0),
                "maxOutputTokens": _model_value(model_cfg, "max_tokens", 450),
            },
        }
        if system_parts:
            payload["system_instruction"] = {"parts": system_parts}
        if self.json_mode == "response_mime_type" and not self._json_mode_dropped:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        return payload

    def _retry(self, operation: Any) -> Any:
        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except TransportError as error:
                last_error = error
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(1.0, 0.1 * (2**attempt) + random.uniform(0.0, 0.1)))
        assert last_error is not None
        raise last_error

    def _finish_success(self, response: LLMResponse, key: str) -> LLMResponse:
        self.breaker.success()
        self.cache.put(key, CachedResponse(response.text, response.prompt_tokens, response.completion_tokens, response.model, self.provider))
        return response

    def _cached(self, key: str, model: str, started: float) -> LLMResponse | None:
        cached = self.cache.get(key)
        if cached is None:
            return None
        response = LLMResponse(cached.text, cached.prompt_tokens, cached.completion_tokens, model or cached.model, (float(self._clock()) - started) * 1000.0, True, self.provider)
        self._record(started=started, model=response.model, prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens, cached=True, success=True)
        return response

    def chat(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> LLMResponse:
        model = str(_model_value(model_cfg, "name", ""))
        started = float(self._clock())
        prompt_tokens = _token_count(messages)
        if not self.api_key:
            error = MissingAPIKeyError("GEMINI_API_KEY is not configured")
            self._record(started=started, model=model, prompt_tokens=prompt_tokens, completion_tokens=0, cached=False, success=False, error=error)
            raise error
        key = self._cache_key(messages, model)
        cached = self._cached(key, model, started)
        if cached is not None:
            return cached
        try:
            self.breaker.before_call()
            text, response_prompt_tokens, completion_tokens = self._retry(lambda: self._send_with_json_fallback(messages, model_cfg))
            response = LLMResponse(text, response_prompt_tokens or prompt_tokens, completion_tokens or _token_count(text), model, (float(self._clock()) - started) * 1000.0, False, self.provider)
            self._record(started=started, model=model, prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens, cached=False, success=True)
            return self._finish_success(response, key)
        except BaseException as error:
            if not isinstance(error, CircuitOpenError):
                self.breaker.failure()
            self._record(started=started, model=model, prompt_tokens=prompt_tokens, completion_tokens=0, cached=False, success=False, error=error)
            raise

    def _send_with_json_fallback(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> tuple[str, int, int]:
        try:
            return self._send(messages, model_cfg)
        except UnsupportedResponseFormatError:
            if self._json_mode_dropped:
                raise
            self._json_mode_dropped = True
            logger.warning("Gemini rejected responseMimeType; using prompt_only for this session")
            return self._send(messages, model_cfg)

    def _send(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> tuple[str, int, int]:
        raise NotImplementedError

    def _call_json(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> dict[str, Any] | None:
        try:
            response = self.chat(messages, model_cfg)
            return parse_json(response.text)
        except BaseException:
            return None

    def close(self) -> None:
        self.cache.close()

    def __enter__(self) -> "_GeminiBaseClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class GeminiLLMClient(_GeminiBaseClient):
    """Direct HTTP adapter for Gemini's native ``generateContent`` API."""

    def __init__(self, config: Any = None, *, http_client: httpx.Client | None = None, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._http = http_client or httpx.Client(verify=self._verify_setting(), timeout=self.timeout_s)
        self._owns_http = http_client is None

    def _send(self, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> tuple[str, int, int]:
        model = str(_model_value(model_cfg, "name", "")).removeprefix("models/")
        url = f"{self.base_url}/models/{quote(model, safe='')}:generateContent"
        try:
            response = self._http.post(
                url,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=self._request_payload(messages, model_cfg),
                timeout=self.timeout_s,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise TransportError(str(error)) from error
        if 400 <= response.status_code < 500:
            body = response.text.lower()
            if "responsemimetype" in body and response.status_code in {400, 422}:
                raise UnsupportedResponseFormatError(response.text)
            raise GeminiHTTPError(response.status_code, response.text[:500])
        if response.status_code >= 500:
            raise TransportError(f"Gemini returned HTTP {response.status_code}")
        try:
            payload = response.json()
            parts = payload["candidates"][0]["content"]["parts"]
            message = "".join(str(part.get("text", "")) for part in parts if isinstance(part, Mapping))
            if not message:
                raise ValueError("empty response")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LLMError("Gemini returned malformed generateContent JSON") from error
        usage = payload.get("usageMetadata") or {}
        return str(message), int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
        super().close()


def create_client(config: Any = None, **kwargs: Any) -> GeminiLLMClient:
    """Build the direct Gemini Developer API client."""

    return GeminiLLMClient(config, **kwargs)


def _call_json(client: LLMClient, messages: Sequence[Mapping[str, Any]], model_cfg: Any) -> dict[str, Any] | None:
    """Fail-closed JSON call helper used by agents and tests."""

    try:
        response = client.chat(messages, model_cfg)
        return parse_json(response.text)
    except BaseException:
        return None
