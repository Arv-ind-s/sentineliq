from __future__ import annotations

import numpy as np

from scripts.train import FEATURE_NAMES, _tune_threshold, _window_features


def test_task_local_feature_adapter_is_stable_and_41_values() -> None:
    window = np.ones((720, 8), dtype=np.float32)
    window[:, 6] = 1_000.0

    features = _window_features(window)

    assert len(FEATURE_NAMES) == 41
    assert len(set(FEATURE_NAMES)) == 41
    assert features.shape == (41,)
    assert np.isfinite(features).all()


def test_threshold_tuning_enforces_precision_floor() -> None:
    labels = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int8)
    probabilities = np.asarray([0.01, 0.10, 0.20, 0.75, 0.90, 0.99], dtype=np.float64)

    threshold, metrics = _tune_threshold(labels, probabilities, min_precision=0.80)

    assert 0.0 <= threshold <= 1.0
    assert metrics["precision"] >= 0.80
