"""Fairness checks for the model's operational feature boundary.

The production predictor receives machine metadata alongside the numeric
feature vector.  Crew, shift, and operator identity must remain outside that
vector and must not change a prediction for the same machine physics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.features import FEATURE_NAMES, extract
from core.predict import load_predictor
from core.preprocess import CHANNELS


CREWS = ("CREW-A", "CREW-B", "CREW-C")
SENSITIVE_FEATURE_TERMS = ("crew", "shift", "operator")
PREDICTION_RATE_NOISE_BAND = 0.05
PREDICTION_THRESHOLD = 0.42


def _feature_vectors() -> list[np.ndarray]:
    """Build a balanced, deterministic set of physically plausible windows."""

    sample_count = 24
    sample_positions = np.linspace(0.0, 2.0 * np.pi, 60)
    vectors: list[np.ndarray] = []
    for index in range(sample_count):
        severity = index % 8
        frame = pd.DataFrame(
            {
                "vibration_rms_mm_s": 2.5 + severity * 0.65 + 0.08 * np.sin(sample_positions),
                "vibration_kurtosis": 3.0 + severity * 0.35 + 0.04 * np.cos(sample_positions),
                "temp_bearing_c": 45.0 + severity * 4.0 + 0.15 * np.sin(sample_positions),
                "temp_motor_c": 50.0 + severity * 3.0 + 0.15 * np.cos(sample_positions),
                "pressure_bar": 5.0 + severity * 0.35 + 0.02 * np.sin(sample_positions),
                "current_a": 15.0 + severity * 2.0 + 0.1 * np.cos(sample_positions),
                "rpm": 1_450.0 + 8.0 * np.sin(sample_positions),
                "ambient_temp_c": 20.0 + 0.1 * np.cos(sample_positions),
            }
        )
        assert tuple(channel for channel in CHANNELS if channel in frame) == CHANNELS
        vectors.append(extract(frame))
    return vectors


def test_feature_names_exclude_crew_shift_and_operator() -> None:
    """The model contract contains no direct workforce identity features."""

    assert FEATURE_NAMES
    assert all(
        not any(term in feature_name.lower() for term in SENSITIVE_FEATURE_TERMS)
        for feature_name in FEATURE_NAMES
    )


def test_prediction_positive_rates_are_equal_by_crew_within_noise() -> None:
    """Identical sensor evidence must produce equivalent rates for each crew."""

    predictor = load_predictor()
    vectors = _feature_vectors()
    rates: dict[str, float] = {}
    for crew in CREWS:
        positives = 0
        for index, vector in enumerate(vectors):
            score = predictor.score(
                vector,
                {
                    "crew_id": crew,
                    "shift_id": f"SHIFT-{index % 3 + 1}",
                    "operator_id": f"operator-{crew.lower()}",
                },
            )
            positives += int(float(score.p_failure_24h) >= PREDICTION_THRESHOLD)
        rates[crew] = positives / len(vectors)

    assert all(0.0 <= rate <= 1.0 for rate in rates.values())
    assert max(rates.values()) - min(rates.values()) <= PREDICTION_RATE_NOISE_BAND, rates
