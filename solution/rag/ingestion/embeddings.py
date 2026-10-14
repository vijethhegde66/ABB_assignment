"""Embedding providers.

``hashing`` (default) is a deterministic, dependency-free embedding: signed
feature hashing of stemmed word unigrams, word bigrams and character 4-grams
with sublinear TF and L2 normalisation. It captures lexical and sub-word
similarity (e.g. "lubrication" ~ "lube oil"), runs offline in CI and in
air-gapped plants, and is fully reproducible.

``sentence-transformers`` can be enabled for true semantic embeddings
(``EMBEDDING_PROVIDER=sentence-transformers``) when the package and model are
available; the index records the provider so mismatched indexes are rejected.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from collections import Counter
from typing import Protocol

import numpy as np

from rag.text import tokenize


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _h(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "little")


class HashingEmbedder:
    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.name = f"hashing-v1-{dim}"

    def _features(self, text: str) -> Counter[str]:
        toks = tokenize(text)
        feats: Counter[str] = Counter()
        for t in toks:
            feats[f"w:{t}"] += 2
        for a, b in itertools.pairwise(toks):
            feats[f"b:{a}_{b}"] += 1
        for t in toks:
            padded = f"^{t}$"
            if len(padded) > 5:
                for i in range(len(padded) - 3):
                    feats[f"c:{padded[i : i + 4]}"] += 0.5  # type: ignore[assignment]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat, count in self._features(text).items():
                h = _h(feat)
                sign = 1.0 if (h >> 63) & 1 else -1.0
                out[row, h % self.dim] += sign * (1.0 + math.log(count)) if count >= 1 else sign * count
            norm = float(np.linalg.norm(out[row]))
            if norm > 0:
                out[row] /= norm
        return out


class SentenceTransformerEmbedder:  # pragma: no cover - optional heavy dependency
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = f"st:{model_name}"

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


def get_embedder(provider: str = "hashing", model: str | None = None) -> Embedder:
    if provider == "hashing":
        return HashingEmbedder()
    if provider == "sentence-transformers":  # pragma: no cover
        return SentenceTransformerEmbedder(model or "sentence-transformers/all-MiniLM-L6-v2")
    raise ValueError(f"unknown embedding provider: {provider}")
