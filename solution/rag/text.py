"""Shared text normalisation used by both ingestion and retrieval (must stay identical on both sides)."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "over",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "too",
        "up",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "about",
        "after",
        "any",
        "all",
        "also",
        "please",
        "show",
        "tell",
        "give",
        "list",
        "find",
        "get",
        "need",
        "want",
        "last",
        "days",
        "day",
        "past",
        "week",
        "weeks",
        "month",
        "months",
        "year",
    ]
)

# Domain synonyms used for query expansion (lower-case, applied to queries only).
SYNONYMS: dict[str, tuple[str, ...]] = {
    "bfp": ("boiler", "feed", "pump"),
    "cavitation": ("suction", "pressure", "npsh"),
    "trip": ("trip", "tripped"),
    "tripped": ("trip",),
    "sop": ("procedure",),
    "procedure": ("procedure", "response"),
    "manual": ("maintenance",),
    "lube": ("lubrication", "oil"),
    "lubrication": ("lube", "oil"),
    "compressor": ("compressor",),
    "recurring": ("recurring", "repeated", "recur"),
    "repeatedly": ("recurring", "repeated"),
    "inspect": ("inspect", "inspection"),
    "related": ("related", "equipment"),
    "priority": ("priority", "response"),
}


def stem(token: str) -> str:
    if len(token) <= 4 or any(c.isdigit() for c in token):
        return token
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            base = token[: -len(suffix)]
            return base + ("y" if suffix == "ies" else "")
    return token


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    out = []
    for raw in _TOKEN.findall(text.lower()):
        parts = [raw, *re.split(r"[-_]", raw)] if ("-" in raw or "_" in raw) else [raw]
        for tok in parts:
            if not keep_stopwords and tok in STOPWORDS:
                continue
            out.append(stem(tok))
    return out


def expand_query_tokens(tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    for t in tokens:
        for syn in SYNONYMS.get(t, ()):
            s = stem(syn)
            if s not in expanded:
                expanded.append(s)
    return expanded
