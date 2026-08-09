"""Offline-safe embedding providers.

TF-IDF is the default and intentional MVP path.  The provider interface keeps
the retrieval layer independent from a future gateway embedding model.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol, Sequence

import numpy as np

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:  # pragma: no cover - exercised in minimal/offline installs
    TfidfVectorizer = None  # type: ignore[assignment,misc]


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    def fit(self, texts: Sequence[str]) -> "EmbeddingProvider": ...

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class TfidfEmbeddingProvider:
    """Deterministic sparse TF-IDF provider used when gateway embeddings are off."""

    def __init__(self, *, max_features: int = 4000) -> None:
        self._vectorizer = TfidfVectorizer(max_features=max_features, lowercase=True, norm="l2") if TfidfVectorizer is not None else None
        self._max_features = max_features
        self._vocabulary: dict[str, int] = {}
        self._idf = np.empty(0, dtype=np.float32)
        self._dimension = 0

    @property
    def dimension(self) -> int:
        return self._dimension

    def fit(self, texts: Sequence[str]) -> "TfidfEmbeddingProvider":
        values = list(texts)
        if self._vectorizer is not None:
            self._vectorizer.fit(values)
            self._dimension = len(self._vectorizer.vocabulary_)
            return self
        token_sets = [set(re.findall(r"\b\w+\b", value.lower())) for value in values]
        document_frequency = Counter(token for tokens in token_sets for token in tokens)
        selected = sorted(document_frequency, key=lambda token: (-document_frequency[token], token))[: self._max_features]
        self._vocabulary = {token: index for index, token in enumerate(selected)}
        self._idf = np.asarray(
            [math.log((1 + len(values)) / (1 + document_frequency[token])) + 1 for token in selected],
            dtype=np.float32,
        )
        self._dimension = len(selected)
        return self

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if self._dimension == 0:
            raise RuntimeError("TF-IDF provider must be fitted before embedding")
        values = list(texts)
        if self._vectorizer is not None:
            return self._vectorizer.transform(values).toarray().astype(np.float32, copy=False)
        matrix = np.zeros((len(values), self._dimension), dtype=np.float32)
        for row, value in enumerate(values):
            counts = Counter(re.findall(r"\b\w+\b", value.lower()))
            for token, count in counts.items():
                column = self._vocabulary.get(token)
                if column is not None:
                    matrix[row, column] = count * self._idf[column]
            norm = float(np.linalg.norm(matrix[row]))
            if norm:
                matrix[row] /= norm
        return matrix

    def fit_transform(self, texts: Sequence[str]) -> np.ndarray:
        self.fit(texts)
        return self.embed(texts)

    # ``transform`` is a familiar alias for retrieval callers.
    transform = embed


def create_embeddings(*, enabled: bool = False, max_features: int = 4000) -> TfidfEmbeddingProvider:
    """Create the configured offline provider.

    Gateway embeddings are intentionally not enabled in the MVP because the
    configured gateway is confirmed for chat only.  ``enabled`` remains an
    explicit seam so a future provider can be added without changing callers.
    """

    if enabled:
        raise RuntimeError("gateway embeddings are not available; use the TF-IDF provider")
    return TfidfEmbeddingProvider(max_features=max_features)
