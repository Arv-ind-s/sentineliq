from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from core.config import ROOT, ConfigError, load_config, settings


def _scratch_config(contents: str) -> Path:
    scratch_dir = ROOT / ".test-artifacts"
    scratch_dir.mkdir(exist_ok=True)
    config_file = scratch_dir / f"config-{uuid.uuid4().hex}.yaml"
    config_file.write_text(contents, encoding="utf-8")
    return config_file


def test_loads_committed_config() -> None:
    config = load_config()

    assert config.data.seed == 42
    assert config.plant.machines == 15
    assert config.predict.threshold_tau_default == 0.42
    assert config.llm.provider == "gemini"
    assert config.llm.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert config.llm.models.coordinator.name == "gemini-2.5-flash-lite"
    assert config.llm.models.embeddings.enabled is False
    assert config.db_url == "sqlite:///data/plant.db"
    assert settings.predict.threshold_tau_default == 0.42


def test_config_yaml_keeps_threshold_tau_as_model_artifact() -> None:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    assert raw["predict"]["threshold_tau_default"] == 0.42
    assert "threshold_tau" not in raw["predict"]


def test_rejects_literal_secret_values() -> None:
    config_file = _scratch_config(
        (ROOT / "config.yaml").read_text(encoding="utf-8") + "\njwt_secret: abc\n",
    )

    try:
        with pytest.raises(ConfigError, match="secret in config.yaml: jwt_secret"):
            load_config(config_file)
    finally:
        config_file.unlink(missing_ok=True)


def test_missing_required_key_has_named_error() -> None:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    del raw["plant"]["machines"]
    config_file = _scratch_config(yaml.safe_dump(raw))

    try:
        with pytest.raises(ConfigError, match="config error: invalid plant.machines"):
            load_config(config_file)
    finally:
        config_file.unlink(missing_ok=True)


def test_startup_config_error_is_one_line_without_traceback() -> None:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    del raw["plant"]["machines"]
    config_file = _scratch_config(yaml.safe_dump(raw))
    env = os.environ.copy()
    env["SENTINEL_CONFIG_PATH"] = str(config_file)

    try:
        result = subprocess.run(
            [sys.executable, "-c", "from core.config import settings; print(settings.plant.machines)"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        config_file.unlink(missing_ok=True)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "config error: invalid plant.machines" in output
    assert "Traceback" not in output
