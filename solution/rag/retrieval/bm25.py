"""Okapi BM25 over pre-tokenised chunks."""

from __future__ import annotations

import math
from collections import Counter


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avgdl = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], idx: int) -> float:
        tf, dl = self.tf[idx], self.len[idx]
        s = 0.0
        for t in set(query):
            f = tf.get(t)
            if not f:
                continue
            s += (
                self.idf.get(t, 0.0)
                * f
                * (self.k1 + 1)
                / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
            )
        return s

    def scores(self, query: list[str]) -> list[float]:
        return [self.score(query, i) for i in range(len(self.tf))]
