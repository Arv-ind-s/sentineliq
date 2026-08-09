from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
SECRET_KEY_PARTS = ("api_key", "secret", "token", "password", "credential")


class ConfigError(RuntimeError):
    """Raised when startup configuration is missing, malformed, or unsafe."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    config_path: str = Field(str(DEFAULT_CONFIG_PATH), validation_alias="SENTINEL_CONFIG_PATH")
    llm_base_url: str = Field("https://llm-gateway.example.com", validation_alias="LLM_BASE_URL")
    llm_api_key: str = Field("", validation_alias="LLM_API_KEY")
    erp_base_url: str = Field("http://127.0.0.1:8001", validation_alias="ERP_BASE_URL")
    erp_api_key: str = Field("", validation_alias="ERP_API_KEY")
    jwt_secret: str = Field("", validation_alias="JWT_SECRET")
    db_url: str = Field("sqlite:///data/plant.db", validation_alias="DB_URL")
    ssl_cert_file: str | None = Field(None, validation_alias="SSL_CERT_FILE")


class PlantConfig(StrictModel):
    timezone: str
    machines: int = Field(gt=0)
    hourly_downtime_cost_default_gbp: int = Field(gt=0)


class DataConfig(StrictModel):
    seed: int
    days: int = Field(gt=0)
    sample_seconds: int = Field(gt=0)
    fault_events: int = Field(gt=0)
    drift_intervals: int = Field(ge=0)
    out_dir: str


class PreprocessConfig(StrictModel):
    grid_seconds: int = Field(gt=0)
    hampel_window: int = Field(gt=0)
    hampel_sigma: float = Field(gt=0)
    max_interpolate_minutes: int = Field(ge=0)
    min_completeness: float = Field(gt=0, le=1)
    frozen_sensor_minutes: int = Field(gt=0)
    source_offsets_s: dict[str, int]


class FeaturesConfig(StrictModel):
    version: str
    window_hours: int = Field(gt=0)
    stride_hours: int = Field(gt=0)


class PredictConfig(StrictModel):
    horizon_hours: int = Field(gt=0)
    min_precision: float = Field(gt=0, le=1)
    threshold_tau_default: float = Field(gt=0, le=1)
    artifacts_dir: str
    fallback_thresholds: dict[str, tuple[float, float]]


class AutonomyConfig(StrictModel):
    a0_confidence_below: float = Field(gt=0, le=1)
    a0_cost_above_gbp: int = Field(gt=0)
    safety_critical_modes: list[str]


class FusionRulesConfig(StrictModel):
    min_completeness: float = Field(gt=0, le=1)
    rul_pressure_days: int = Field(gt=0)
    repeat_failure_window_days: int = Field(gt=0)
    repeat_failure_risk_add: int = Field(ge=0)
    evidence_conflict_factor: float = Field(gt=0, le=1)
    agreement_factors: dict[str, float]
    evidence_count_saturation: int = Field(gt=0)
    criticality_weights: dict[str, float]
    criticality_score_thresholds: dict[str, int]
    repeat_failure_min_count: int = Field(gt=0)
    sensor_drift_min_replacements: int = Field(gt=0)
    confidence_probability_midpoint: float = Field(ge=0, le=1)
    confidence_probability_scale: float = Field(gt=0)


class FusionConfig(StrictModel):
    weights: dict[str, int]
    bands: dict[str, int]
    autonomy: AutonomyConfig
    rules: FusionRulesConfig


class LLMModelConfig(StrictModel):
    name: str
    temperature: float = Field(ge=0)
    max_tokens: int = Field(gt=0)


class EmbeddingsConfig(StrictModel):
    enabled: bool
    name: str
    dim: int = Field(gt=0)


class LLMModelsConfig(StrictModel):
    coordinator: LLMModelConfig
    log_summary: LLMModelConfig
    embeddings: EmbeddingsConfig


class CircuitBreakerConfig(StrictModel):
    failures: int = Field(gt=0)
    cooldown_s: int = Field(gt=0)


class CacheConfig(StrictModel):
    enabled: bool
    ttl_s: int = Field(gt=0)


class PriceConfig(StrictModel):
    prompt: float = Field(ge=0)
    completion: float = Field(ge=0)


class LLMConfig(StrictModel):
    provider: Literal["langchain", "httpx"]
    base_url: str
    api_key: str = ""
    chat_path: str
    tls_verify: bool
    models: LLMModelsConfig
    json_mode: Literal["auto", "response_format", "prompt_only"]
    prompt_version: str
    timeout_s: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    log_summary_call_enabled: bool
    max_context_tokens: int = Field(gt=0)
    circuit_breaker: CircuitBreakerConfig
    cache: CacheConfig
    price_per_1k: PriceConfig


class ERPConfig(StrictModel):
    base_url: str
    api_key: str = ""
    timeout_s: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    field_map: dict[str, str]


class SecurityConfig(StrictModel):
    jwt_ttl_hours: int = Field(gt=0)
    rate_limits: dict[str, int]
    cors_origins: list[str]


class StreamConfig(StrictModel):
    speedup: int = Field(gt=0)
    replay_start_day: float = Field(ge=0)
    queue_maxsize: int = Field(gt=0)
    demo_alert_window_s: tuple[int, int]


class UIFeaturesConfig(StrictModel):
    digital_twin: bool
    rehearsal: bool
    ledger: bool


class UIConfig(StrictModel):
    features: UIFeaturesConfig


class AppConfig(StrictModel):
    plant: PlantConfig
    data: DataConfig
    preprocess: PreprocessConfig
    features: FeaturesConfig
    predict: PredictConfig
    fusion: FusionConfig
    llm: LLMConfig
    erp: ERPConfig
    security: SecurityConfig
    stream: StreamConfig
    ui: UIConfig
    db_url: str = "sqlite:///data/plant.db"
    jwt_secret: str = ""
    ssl_cert_file: str | None = None


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SECRET_KEY_PARTS)


def _walk_for_literal_secrets(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _is_secret_key(str(key)) and isinstance(child, str):
                if child and ENV_PATTERN.fullmatch(child) is None:
                    findings.append(child_path)
            findings.extend(_walk_for_literal_secrets(child, child_path))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            findings.extend(_walk_for_literal_secrets(child, f"{path}[{idx}]"))
    return findings


def _env_values(env: EnvSettings) -> dict[str, str]:
    return {
        "LLM_BASE_URL": env.llm_base_url,
        "LLM_API_KEY": env.llm_api_key,
        "ERP_BASE_URL": env.erp_base_url,
        "ERP_API_KEY": env.erp_api_key,
        "JWT_SECRET": env.jwt_secret,
        "DB_URL": env.db_url,
        "SSL_CERT_FILE": env.ssl_cert_file or "",
    }


def _interpolate_env(value: Any, env_values: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _interpolate_env(child, env_values) for key, child in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(child, env_values) for child in value]
    if isinstance(value, str):
        match = ENV_PATTERN.fullmatch(value)
        if match:
            return env_values.get(match.group(1), "")
    return value


def _format_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
    return f"config error: invalid {location}: {first.get('msg', 'validation failed')}"


def load_config(config_path: str | Path | None = None, env: EnvSettings | None = None) -> AppConfig:
    env_settings = env or EnvSettings()
    path = Path(config_path or env_settings.config_path)

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"config error: missing config.yaml at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"config error: malformed config.yaml: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"config error: {path} must contain a YAML mapping")

    literal_secret_paths = _walk_for_literal_secrets(raw)
    if literal_secret_paths:
        joined = ", ".join(literal_secret_paths)
        raise ConfigError(f"secret in config.yaml: {joined}")

    data = _interpolate_env(raw, _env_values(env_settings))
    data["db_url"] = env_settings.db_url
    data["jwt_secret"] = env_settings.jwt_secret
    data["ssl_cert_file"] = env_settings.ssl_cert_file

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


@lru_cache
def get_config() -> AppConfig:
    return load_config()


def _load_startup_settings() -> AppConfig:
    try:
        return get_config()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None


settings = _load_startup_settings()
