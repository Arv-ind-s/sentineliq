"""Verified model serving with a transparent rule-based fallback.

The fallback thresholds are configured in ``config.yaml`` under
``predict.fallback_thresholds``.  Each channel has a ``[warning, critical]``
pair in its native engineering unit:

* vibration RMS: 4.5 / 8.0 mm/s;
* vibration kurtosis: 6 / 12;
* bearing temperature: 70 / 85 °C;
* motor temperature: 80 / 100 °C;
* pressure: 8 / 12 bar;
* current: 30 / 50 A;
* speed: 1450 / 1750 rpm;
* ambient temperature: 35 / 45 °C.

Those values are deliberately conservative demo defaults, not a replacement
for a calibrated plant model.  The serving path verifies every artifact hash
and the feature-version contract before loading a model.  A failed load is
observable through ``predictor: "rule_fallback"`` and never silently appears
as a model prediction.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, Protocol, Sequence

import joblib
import numpy as np

from core.config import ROOT, settings
from core.features import FEATURE_NAMES, FEATURE_VERSION, FeatureError, FeatureVersionMismatch, load_manifest


logger = logging.getLogger(__name__)


class PredictorError(RuntimeError):
    """Base class for failures in the verified model-serving path."""


class ArtifactVerificationError(PredictorError):
    """Raised when model artifacts cannot be trusted or loaded."""


class ArtifactMissingError(ArtifactVerificationError):
    """Raised when a manifest artifact is absent."""


class ArtifactHashMismatch(ArtifactVerificationError):
    """Raised when an artifact digest differs from its manifest digest."""


class ModelFeatureVersionMismatch(ArtifactVerificationError):
    """Raised when the model was trained against another feature contract."""


class Predictor(Protocol):
    """Minimal score seam shared by the sensor agent and API."""

    def score(self, features: np.ndarray, machine_meta: Mapping[str, Any]) -> "PredictionResult": ...


@dataclass(frozen=True)
class PredictionResult:
    """Stable prediction payload consumed by API and agent boundaries."""

    p_failure_24h: float
    rul_p10: float
    rul_p50: float
    rul_p90: float
    mode_probs: dict[str, float]
    top_features: list[dict[str, Any]]
    confidence: float
    predictor: str
    model_version: str


def _validated_features(features: np.ndarray | Sequence[float]) -> np.ndarray:
    vector = np.asarray(features, dtype=np.float32)
    if vector.shape != (len(FEATURE_NAMES),):
        raise ValueError(f"feature vector must have shape ({len(FEATURE_NAMES)},), got {vector.shape}")
    if not np.isfinite(vector).all():
        raise ValueError("feature vector must contain only finite values")
    return vector.reshape(1, -1)


def _positive_probability(model: Any, row: np.ndarray) -> float:
    probabilities = np.asarray(model.predict_proba(row), dtype=float)
    if probabilities.ndim != 2 or probabilities.shape[0] != 1:
        raise PredictorError("failure model returned an invalid probability shape")
    classes = list(getattr(model, "classes_", (0, 1)))
    try:
        positive_index = classes.index(1)
    except ValueError:
        positive_index = min(1, probabilities.shape[1] - 1)
    return float(np.clip(probabilities[0, positive_index], 0.0, 1.0))


def _normalise_probabilities(values: Mapping[str, float]) -> dict[str, float]:
    cleaned = {str(key): max(0.0, float(value)) for key, value in values.items() if np.isfinite(float(value))}
    total = sum(cleaned.values())
    if total <= 0:
        return {"unknown": 1.0}
    return {key: value / total for key, value in cleaned.items()}


def _artifact_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_artifact_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ArtifactVerificationError(f"model artifact path escapes artifact directory: {name}")
    return path


def _verify_artifacts(root: Path, manifest: Mapping[str, Any]) -> dict[str, Path]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise ArtifactVerificationError("model manifest has no artifacts")
    paths: dict[str, Path] = {}
    for raw_name, raw_expected in artifacts.items():
        name = str(raw_name)
        expected = str(raw_expected).lower()
        path = _safe_artifact_path(root, name)
        if not path.is_file():
            raise ArtifactMissingError(f"model artifact missing: {name}")
        actual = _artifact_digest(path)
        if actual != expected:
            raise ArtifactHashMismatch(f"artifact hash mismatch for {name}: expected {expected}, got {actual}")
        paths[Path(name).stem] = path
    return paths


class VerifiedModelPredictor:
    """Load and score only artifacts that pass manifest verification."""

    predictor_name: Final[str] = "histgradientboosting"

    def __init__(self, artifact_dir: str | Path | None = None) -> None:
        self.artifact_dir = Path(artifact_dir or (ROOT / settings.predict.artifacts_dir)).resolve()
        manifest_path = self.artifact_dir / "MANIFEST.json"
        try:
            manifest = load_manifest(manifest_path)
        except FeatureVersionMismatch as exc:
            raise ModelFeatureVersionMismatch(str(exc)) from exc
        except FeatureError as exc:
            raise ArtifactVerificationError(str(exc)) from exc
        if manifest.get("feature_version") != FEATURE_VERSION:
            raise ModelFeatureVersionMismatch(f"feature version mismatch: expected {FEATURE_VERSION!r}, got {manifest.get('feature_version')!r}")
        self.manifest = manifest
        paths = _verify_artifacts(self.artifact_dir, manifest)
        required = {"failure_calibrated", "fault_mode", "rul_p10", "rul_p50", "rul_p90", "feature_medians", "permutation_importance"}
        missing = sorted(required - paths.keys())
        if missing:
            raise ArtifactVerificationError(f"model manifest missing required artifacts: {', '.join(missing)}")
        try:
            self.failure_model = joblib.load(paths["failure_calibrated"])
            self.fault_model = joblib.load(paths["fault_mode"])
            self.rul_models = tuple(joblib.load(paths[name]) for name in ("rul_p10", "rul_p50", "rul_p90"))
            self.feature_medians = np.asarray(joblib.load(paths["feature_medians"]), dtype=np.float32)
            self.importance = joblib.load(paths["permutation_importance"])
        except Exception as exc:
            raise ArtifactVerificationError(f"model artifact load failed: {type(exc).__name__}: {exc}") from exc
        if self.feature_medians.shape != (len(FEATURE_NAMES),):
            raise ArtifactVerificationError("feature_medians artifact does not match the fv1 feature shape")
        self.model_version = str(manifest.get("model_version") or manifest.get("feature_version", "unknown"))

    def score(self, features: np.ndarray, machine_meta: Mapping[str, Any]) -> PredictionResult:
        row = _validated_features(features)
        row = np.where(np.isfinite(row), row, self.feature_medians.reshape(1, -1))
        p_failure = _positive_probability(self.failure_model, row)
        mode_probabilities = _normalise_probabilities({str(label): float(value) for label, value in zip(self.fault_model.classes_, self.fault_model.predict_proba(row)[0])})
        rul = [max(0.0, float(np.asarray(model.predict(row)).reshape(-1)[0])) for model in self.rul_models]
        rul_p10, rul_p50, rul_p90 = sorted(rul)
        importance_names = self.importance.get("feature_names", []) if isinstance(self.importance, Mapping) else []
        importance_values = self.importance.get("importance_mean", []) if isinstance(self.importance, Mapping) else []
        ranked = sorted(zip(importance_names, importance_values, row[0]), key=lambda item: abs(float(item[1])), reverse=True)[:5]
        top_features = [
            {"name": str(name), "value": round(float(value), 4), "contribution": round(float(value) * float(importance), 4), "direction": "increases_risk" if float(value) * float(importance) >= 0 else "decreases_risk"}
            for name, importance, value in ranked
        ]
        return PredictionResult(
            p_failure_24h=p_failure,
            rul_p10=rul_p10,
            rul_p50=rul_p50,
            rul_p90=rul_p90,
            mode_probs=mode_probabilities,
            top_features=top_features,
            confidence=float(np.clip(0.5 + abs(p_failure - 0.5), 0.0, 1.0)),
            predictor=self.predictor_name,
            model_version=self.model_version,
        )


class RuleFallbackPredictor:
    """Explainable threshold predictor used when verified models are unavailable."""

    predictor_name: Final[str] = "rule_fallback"

    def __init__(self, *, reason: str = "verified model artifacts unavailable") -> None:
        self.reason = reason
        self.model_version = f"rule-fallback-{settings.features.version}"
        self.thresholds = dict(settings.predict.fallback_thresholds)

    def score(self, features: np.ndarray, machine_meta: Mapping[str, Any]) -> PredictionResult:
        vector = _validated_features(features)[0]
        signals: list[tuple[str, float, float]] = []
        for channel, pair in self.thresholds.items():
            mean_name = f"{channel}_mean"
            if mean_name not in FEATURE_NAMES:
                continue
            warning, critical = (float(pair[0]), float(pair[1]))
            if critical <= warning:
                raise PredictorError(f"invalid rule threshold for {channel}: critical must exceed warning")
            value = float(vector[FEATURE_NAMES.index(mean_name)])
            signal = float(np.clip((value - warning) / (critical - warning), 0.0, 1.0))
            signals.append((channel, value, signal))
        signals.sort(key=lambda item: item[2], reverse=True)
        max_signal = signals[0][2] if signals else 0.0
        p_failure = float(np.clip(0.05 + 0.9 * max_signal, 0.05, 0.99))
        mode_weights: dict[str, float] = {}
        for channel, _, signal in signals:
            mode = {
                "vibration_rms_mm_s": "bearing_wear",
                "vibration_kurtosis": "rotor_imbalance",
                "temp_bearing_c": "bearing_wear",
                "temp_motor_c": "motor_overheat",
                "pressure_bar": "seal_leak",
                "current_a": "lubrication_degradation",
                "rpm": "rotor_imbalance",
                "ambient_temp_c": "motor_overheat",
            }.get(channel, "unknown")
            mode_weights[mode] = mode_weights.get(mode, 0.0) + signal
        if not any(mode_weights.values()):
            mode_weights = {"unknown": 1.0}
        p50 = max(1.0, 45.0 * (1.0 - p_failure))
        top_features = [
            {"name": channel, "value": round(value, 4), "contribution": round(signal, 4), "direction": "increases_risk" if signal > 0 else "decreases_risk"}
            for channel, value, signal in signals[:5]
        ]
        return PredictionResult(
            p_failure_24h=p_failure,
            rul_p10=max(0.5, p50 * 0.45),
            rul_p50=p50,
            rul_p90=max(p50, p50 * 1.8),
            mode_probs=_normalise_probabilities(mode_weights),
            top_features=top_features,
            confidence=float(np.clip(0.35 + 0.35 * max_signal, 0.35, 0.70)),
            predictor=self.predictor_name,
            model_version=self.model_version,
        )


class ResilientPredictor:
    """Keep serving honestly if a loaded artifact fails during inference."""

    def __init__(self, primary: Predictor, fallback: RuleFallbackPredictor) -> None:
        self.primary = primary
        self.fallback = fallback

    def score(self, features: np.ndarray, machine_meta: Mapping[str, Any]) -> PredictionResult:
        try:
            return self.primary.score(features, machine_meta)
        except Exception as exc:
            logger.exception("model_inference_failed; switching to rule fallback")
            self.fallback.reason = f"model inference failed: {type(exc).__name__}: {exc}"
            return self.fallback.score(features, machine_meta)


def load_verified_predictor(artifact_dir: str | Path | None = None) -> VerifiedModelPredictor:
    """Load a model predictor or raise a named verification error."""

    return VerifiedModelPredictor(artifact_dir)


def load_predictor(artifact_dir: str | Path | None = None) -> Predictor:
    """Return a verified predictor, falling back with an explicit reason."""

    try:
        primary = load_verified_predictor(artifact_dir)
    except (ArtifactVerificationError, OSError) as exc:
        # A model file may exist but still be unavailable to the running
        # process (for example, a Windows ACL or a transient network-mounted
        # artifact volume).  This is operationally equivalent to an
        # unverifiable artifact: do not let it take down every agent that
        # needs a prediction; serve the explicitly labelled rule fallback.
        logger.warning("rule_fallback_active reason=%s", exc)
        return RuleFallbackPredictor(reason=str(exc))
    return ResilientPredictor(primary, RuleFallbackPredictor(reason="model inference failed"))


__all__ = [
    "ArtifactHashMismatch",
    "ArtifactMissingError",
    "ArtifactVerificationError",
    "ModelFeatureVersionMismatch",
    "PredictionResult",
    "Predictor",
    "PredictorError",
    "RuleFallbackPredictor",
    "VerifiedModelPredictor",
    "load_predictor",
    "load_verified_predictor",
]
