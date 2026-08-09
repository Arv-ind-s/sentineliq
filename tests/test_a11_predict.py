from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from core.predict import ArtifactHashMismatch, ModelFeatureVersionMismatch, RuleFallbackPredictor, load_predictor, load_verified_predictor


ROOT = Path(__file__).resolve().parents[1]


def _copy_artifacts(tmp_path: Path) -> Path:
    destination = tmp_path / "models"
    shutil.copytree(ROOT / "models", destination)
    return destination


def test_verified_predictor_scores_with_hashed_artifacts(tmp_path: Path) -> None:
    predictor = load_verified_predictor(_copy_artifacts(tmp_path))

    result = predictor.score(np.zeros(41, dtype=np.float32), {})

    assert result.predictor == "histgradientboosting"
    assert result.model_version == "fv1"
    assert 0 <= result.p_failure_24h <= 1
    assert result.rul_p10 <= result.rul_p50 <= result.rul_p90


def test_missing_artifact_activates_rule_fallback(tmp_path: Path) -> None:
    artifact_dir = _copy_artifacts(tmp_path)
    (artifact_dir / "failure_calibrated.joblib").unlink()

    predictor = load_predictor(artifact_dir)
    result = predictor.score(np.zeros(41, dtype=np.float32), {})

    assert isinstance(predictor, RuleFallbackPredictor)
    assert result.predictor == "rule_fallback"


def test_unreadable_artifact_activates_rule_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(_: Path | None = None) -> object:
        raise PermissionError("access denied")

    monkeypatch.setattr("core.predict.load_verified_predictor", unreadable)

    predictor = load_predictor()

    assert isinstance(predictor, RuleFallbackPredictor)
    assert "access denied" in predictor.reason


def test_corrupt_artifact_refuses_verified_load_and_falls_back(tmp_path: Path) -> None:
    artifact_dir = _copy_artifacts(tmp_path)
    path = artifact_dir / "failure_calibrated.joblib"
    payload = bytearray(path.read_bytes())
    payload[len(payload) // 2] ^= 0x01
    path.write_bytes(payload)

    with pytest.raises(ArtifactHashMismatch, match="hash mismatch"):
        load_verified_predictor(artifact_dir)
    assert load_predictor(artifact_dir).score(np.zeros(41, dtype=np.float32), {}).predictor == "rule_fallback"


def test_feature_version_mismatch_refuses_verified_load(tmp_path: Path) -> None:
    artifact_dir = _copy_artifacts(tmp_path)
    manifest_path = artifact_dir / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["feature_version"] = "fv2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ModelFeatureVersionMismatch, match="feature version mismatch"):
        load_verified_predictor(artifact_dir)
    assert load_predictor(artifact_dir).score(np.zeros(41, dtype=np.float32), {}).predictor == "rule_fallback"


def test_api_prediction_stamps_rule_fallback_and_ui_has_banner(monkeypatch) -> None:
    monkeypatch.setattr(api_main, "_PREDICTOR", RuleFallbackPredictor(reason="test artifact failure"))
    client = TestClient(api_main.app)
    assert client.post("/auth/login", json={"username": "admin", "password": "fixture"}).status_code == 200

    response = client.get("/machines/PUMP-07/prediction")

    assert response.status_code == 200
    assert response.json()["predictor"] == "rule_fallback"
    source = (ROOT / "ui" / "src" / "screens" / "screens.tsx").read_text(encoding="utf-8")
    assert "Fallback predictor active" in source
    assert 'predictor !== "rule_fallback"' in source
