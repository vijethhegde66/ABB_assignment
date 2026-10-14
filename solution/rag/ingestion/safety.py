"""Prompt-injection screening for retrieved / ingested document text.

Retrieved documents are *data*, never instructions. This module provides the
ingestion-time and retrieval-time defence: chunks that contain instruction-like
text aimed at an AI system are flagged at ingestion, excluded from retrieval
by default (quarantine), and any residual instruction sentences are redacted
before text reaches the answer generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier)\b.{0,30}\b(instructions?|rules?|prompts?|guidelines?)",
            re.I,
        ),
        0.6,
    ),
    (
        "ai_addressed",
        re.compile(
            r"\b(note|message|instruction)s?\s+(to|for)\s+(ai|llm|assistants?|chatbots?|language models?|copilots?)",
            re.I,
        ),
        0.4,
    ),
    (
        "role_hijack",
        re.compile(r"\byou are (now|no longer)\b|\bact as\b|\bnew (system )?persona\b", re.I),
        0.4,
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"\b(reveal|print|show|output|leak)\b.{0,30}\b(system prompt|instructions|hidden prompt|api key|secret)",
            re.I,
        ),
        0.5,
    ),
    (
        "tool_invocation",
        re.compile(
            r"\b(call|invoke|execute|run)\b.{0,20}\b(tool|function|submit_\w+|create_\w+|api)\b", re.I
        ),
        0.4,
    ),
    (
        "safety_defeat",
        re.compile(
            r"\b(bypass|disable|defeat|inhibit|override)\b.{0,40}\b(trip|interlock|alarm|protection|safety)",
            re.I,
        ),
        0.3,
    ),
    (
        "special_tokens",
        re.compile(r"<\|?(system|im_start|im_end|assistant)\|?>|\[/?INST\]|</?system>", re.I),
        0.6,
    ),
]

FLAG_THRESHOLD = 0.6
REDACTION = "[redacted: suspected prompt-injection content]"
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True)
class InjectionAssessment:
    score: float
    signals: tuple[str, ...]

    @property
    def flagged(self) -> bool:
        return self.score >= FLAG_THRESHOLD


def assess(text: str) -> InjectionAssessment:
    signals = []
    score = 0.0
    for name, pattern, weight in _PATTERNS:
        if pattern.search(text):
            signals.append(name)
            score += weight
    # Legitimate procedures say "never bypass a trip"; a lone safety_defeat hit is not an injection.
    if signals == ["safety_defeat"]:
        score = 0.0
    return InjectionAssessment(score=round(min(score, 1.0), 2), signals=tuple(signals))


def sanitize(text: str) -> tuple[str, bool]:
    """Redact sentences that carry injection signals; returns (clean_text, changed)."""
    changed = False
    out: list[str] = []
    for m in _SENTENCE.finditer(text):
        sentence = m.group(0)
        a = assess(sentence)
        hits = set(a.signals) - {"safety_defeat"}
        if hits and (a.score >= 0.4 or "override_instructions" in hits):
            out.append(f" {REDACTION}")
            changed = True
        else:
            out.append(sentence)
    return ("".join(out).strip() if changed else text), changed
