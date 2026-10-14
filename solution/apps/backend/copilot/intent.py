"""Intent detection and entity extraction.

A transparent, weighted rule classifier: every intent has cue patterns with
weights; the best score wins and the matched cues are returned as the
rationale (shown in the GUI). Entities (asset phrases, identifiers, site,
unit, severity, status, time window, alarm terms) are extracted with patterns
that describe *shapes* of plant vocabulary - not the sample questions - and
are resolved against the source system through MCP tools later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

INTENTS = (
    "investigate",  # recurring alarms / why / contributing factors over a period
    "active_response",  # what is active now and what to do immediately
    "priority_ranking",  # which alarm has the highest priority (site/unit/asset)
    "related_assets",  # which related assets should be inspected
    "procedure_lookup",  # which procedure applies
    "consistency_check",  # are API recommendations consistent with documents
    "alarm_performance",  # floods, KPIs, rationalization, chattering
    "document_question",  # pure knowledge question answered from documents
)

_CUES: dict[str, list[tuple[str, float]]] = {
    "investigate": [
        (r"\binvestigat\w*", 2.0),
        (r"\bwhy\b", 2.0),
        (r"\brecurr\w*|\brepeated\w*|\bkeep[s]? (occurring|happening)", 2.0),
        (r"\b(root )?causes?\b|\bcontribut\w+", 1.5),
        (r"\bhistor\w*|\bpattern\w*|\btrend\w*", 1.0),
        (r"\blast \d+ (days?|weeks?|months?)|\bover the (last|past)", 0.75),
    ],
    "active_response": [
        (r"\bactive\b|\bcurrent(ly)?\b|\bright now\b|\bstanding\b", 2.0),
        (r"\bimmediate\w*|\burgent\w*|\bwhat should (i|we) do\b", 1.5),
        (r"\brecommend\w*|\bactions?\b", 0.75),
    ],
    "priority_ranking": [
        (r"\bhighest[- ]priority\b|\bmost (urgent|critical|important)\b", 3.0),
        (r"\bpriorit\w*|\brank\w*", 1.5),
        (r"\bwhich alarm\b", 1.0),
    ],
    "related_assets": [
        (r"\brelated (assets?|equipment)\b|\bwhat else\b|\bdownstream\b|\bupstream\b", 3.0),
        (r"\binspect\w*", 1.0),
    ],
    "procedure_lookup": [
        (r"\b(operating )?procedures?\b|\bsop\b", 2.5),
        (r"\bapplies\b|\bapplicable\b|\bwhich document\b", 1.0),
        (r"\bhow (do|should) (i|we) respond\b|\bresponse steps\b", 1.5),
    ],
    "consistency_check": [
        (r"\bconsistent\b|\bconsistency\b|\bagree\w*|\bconflict\w*|\bcontradict\w*", 3.0),
        (r"\bcompare\w*|\bmatch\w*|\balign\w*", 1.0),
        (r"\bmanual\b|\bprocedure\b", 0.5),
    ],
    "alarm_performance": [
        (r"\bfloods?\b|\bkpis?\b|\bchatter\w*|\bnuisance\b|\brationali[sz]\w*", 3.0),
        (r"\balarm rate\b|\bresponse efficiency\b|\bstale\b|\bperformance\b", 1.5),
    ],
    "document_question": [
        (r"\bwhat (is|are) the\b|\bhow (do|to)\b|\bexplain\b|\bdefin\w+", 1.0),
        (r"\blimits?\b|\binterval\b|\bppe\b|\block-?out\b|\bpermit\b", 1.0),
    ],
}

_EQUIPMENT_WORDS = (
    "Boiler Feed Pump|Feed Pump|Pump|Compressor|Motor|Deaerator|Heater|Reactor|Intercooler|Exchanger|Valve|Fan|"
    "Switchboard|MCC|Lube Oil System|Turbine|Boiler"
)
# Specific, named assets: capitalised words ending in an equipment noun, optionally followed by an identifier
# (case-sensitive on purpose: "Boiler Feed Pump 101", "Crude Charge Pump Motor M-501", "Gas Compressor K-201").
_ASSET_PHRASE = re.compile(
    rf"\b((?:[A-Z][\w-]*\s){{0,4}}(?:{_EQUIPMENT_WORDS})(?:\s+(?:[A-Z]{{1,3}}-?\d{{2,4}}|\d{{2,4}}))?)\b"
)
# Generic equipment classes mentioned in lower case ("compressor discharge pressure alarms").
_GENERIC_EQUIPMENT = re.compile(
    r"\b(boiler feed pumps?|feed pumps?|compressors?|motors?|deaerators?|heaters?|reactors?|intercoolers?|"
    r"pumps?|valves?|fans?)\b",
    re.I,
)
_TAG = re.compile(r"\b([A-Z]{1,3}-\d{3,4})\b")
_ASSET_ID = re.compile(r"\b([A-Z]{2}-[A-Z]{2,4}-\d{3})\b")
_ALARM_ID = re.compile(r"\b(ALM-\d{4,10})\b", re.I)
_SITE = re.compile(r"\b([A-Z][a-z]+(?:Plant|Refinery|Terminal|Site|Mill|Works))\b")
_UNIT = re.compile(r"\bunit\s+(\d{1,3})\b", re.I)
_WINDOW = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(hours?|days?|weeks?|months?)\b", re.I)
_WINDOW_WORD = re.compile(r"\b(?:last|past|this)\s+(week|month|quarter|year|day)\b", re.I)

# measurement / event vocabulary used to recognise which alarm the user means
_ALARM_TERMS = [
    "discharge pressure",
    "suction pressure",
    "bearing temperature",
    "winding temperature",
    "vibration",
    "motor trip",
    "trip",
    "level",
    "surge",
    "current",
    "minimum flow",
    "seal leak",
    "lube oil",
    "oil pressure",
    "outlet temperature",
    "discharge temperature",
    "flame",
    "earth fault",
    "undervoltage",
    "position deviation",
    "differential pressure",
    "temperature",
    "pressure",
    "flow",
]
_CONTEXT_REFS = re.compile(
    r"\b(this|that|the same|same|it|its)\b\s*(alarm|asset|pump|motor|compressor|one|equipment)?", re.I
)
_STOP_PHRASE_WORDS = {
    "show",
    "why",
    "which",
    "what",
    "investigate",
    "are",
    "is",
    "the",
    "for",
    "and",
    "recommend",
    "should",
    "related",
    "assets",
    "active",
    "critical",
    "high",
    "recurring",
    "operating",
    "this",
    "that",
    "these",
    "same",
    "a",
    "an",
    "all",
    "any",
    "my",
    "our",
    "of",
}


@dataclass
class Understanding:
    text: str
    intent: str
    confidence: float
    rationale: str
    asset_phrases: list[str] = field(default_factory=list)
    generic_asset: bool = False
    asset_ids: list[str] = field(default_factory=list)
    alarm_id: str | None = None
    site: str | None = None
    unit: str | None = None
    severities: list[str] | None = None
    status: str | None = None
    lookback_days: int | None = None
    alarm_terms: list[str] = field(default_factory=list)
    refers_to_context: bool = False
    wants_recommendations: bool = False

    def entities(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "asset_phrases": self.asset_phrases,
                "asset_ids": self.asset_ids,
                "alarm_id": self.alarm_id,
                "site": self.site,
                "unit": self.unit,
                "severities": self.severities,
                "status": self.status,
                "lookback_days": self.lookback_days,
                "alarm_terms": self.alarm_terms,
                "refers_to_context": self.refers_to_context,
            }.items()
            if v not in (None, [], False)
        }


def _clean_phrase(p: str) -> str:
    words = p.strip().split()
    while words and words[0].lower() in _STOP_PHRASE_WORDS:
        words.pop(0)
    return " ".join(words)


def extract_asset_phrases(text: str) -> tuple[list[str], bool]:
    """Return (phrases, generic). ``generic`` is True when only an equipment class was mentioned."""
    phrases: list[str] = []
    for m in _ASSET_PHRASE.finditer(text):
        p = _clean_phrase(m.group(1))
        if p and p.lower() not in {x.lower() for x in phrases}:
            phrases.append(p)
    for tag in _TAG.findall(text):
        if not tag.startswith("ALM") and not any(tag in p for p in phrases):
            phrases.append(tag)
    if phrases:
        specific = [
            p for p in phrases if not any(p.lower() != q.lower() and p.lower() in q.lower() for q in phrases)
        ]
        return specific[:3], False
    generic = []
    for m in _GENERIC_EQUIPMENT.finditer(text):
        word = m.group(1).lower().rstrip("s") if not m.group(1).lower().endswith("ss") else m.group(1).lower()
        if word not in generic:
            generic.append(word)
    return generic[:2], bool(generic)


def understand(text: str) -> Understanding:
    lower = text.lower()
    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for intent, cues in _CUES.items():
        for pattern, weight in cues:
            m = re.search(pattern, lower)
            if m:
                scores[intent] = scores.get(intent, 0.0) + weight
                hits.setdefault(intent, []).append(m.group(0))

    asset_ids = _ASSET_ID.findall(text)
    alarm_id = _ALARM_ID.search(text).group(1).upper() if _ALARM_ID.search(text) else None  # type: ignore[union-attr]
    phrases, generic = ([], False) if asset_ids else extract_asset_phrases(text)
    site_m = _SITE.search(text)
    unit_m = _UNIT.search(text)

    severities: list[str] | None = None
    if re.search(r"\bhigh[- ]severity\b|\bhigh and critical\b|\bhigh/critical\b|\bserious\b", lower):
        severities = ["high", "critical"]
    elif re.search(r"\bcritical\b", lower):
        severities = ["critical"]
    elif re.search(r"\bhigh\b(?!est)(?![- ]priority)", lower) and "alarm" in lower:
        severities = ["high", "critical"]

    lookback = None
    wm = _WINDOW.search(text)
    if wm:
        n, unit = int(wm.group(1)), wm.group(2).lower()
        lookback = max(1, n // 24) if unit.startswith("hour") else n * {"d": 1, "w": 7, "m": 30}[unit[0]]
    elif ww := _WINDOW_WORD.search(text):
        lookback = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}[ww.group(1).lower()]

    alarm_terms: list[str] = []
    for term in _ALARM_TERMS:
        if re.search(rf"\b{term}\b", lower) and not any(term in t for t in alarm_terms):
            alarm_terms.append(term)

    status = "active" if re.search(r"\bactive\b|\bcurrent(ly)?\b|\bright now\b|\bstanding\b", lower) else None
    context_ref = bool(_CONTEXT_REFS.search(text))
    if generic and context_ref:
        phrases = []  # "this motor trip alarm" refers to the conversation context, not to all motors
    refers = context_ref and not (asset_ids or phrases or alarm_id)

    # disambiguation rules
    if "priority_ranking" in scores and (site_m or unit_m) and not phrases:
        scores["priority_ranking"] += 1.0
    if "consistency_check" in scores and scores["consistency_check"] >= 3.0:
        scores["consistency_check"] += 1.0
    if scores.get("related_assets", 0) >= 3.0:
        scores["related_assets"] += 0.5
    if not (asset_ids or phrases or alarm_id or site_m or unit_m or refers):
        scores["document_question"] = scores.get("document_question", 0.0) + 1.5
    if not scores:
        intent, conf, rationale = (
            ("investigate" if (phrases or asset_ids) else "document_question"),
            0.3,
            "no intent cues; defaulted from entities",
        )
    else:
        intent = max(scores, key=lambda k: (scores[k], -INTENTS.index(k)))
        total = sum(scores.values())
        conf = round(scores[intent] / total, 2) if total else 0.0
        rationale = f"cues: {', '.join(hits.get(intent, [])) or 'entity defaults'}"

    return Understanding(
        text=text,
        intent=intent,
        confidence=conf,
        rationale=rationale,
        asset_phrases=phrases,
        generic_asset=generic and bool(phrases),
        asset_ids=asset_ids,
        alarm_id=alarm_id,
        site=site_m.group(1) if site_m else None,
        unit=f"Unit {unit_m.group(1)}" if unit_m else None,
        severities=severities,
        status=status,
        lookback_days=lookback,
        alarm_terms=alarm_terms,
        refers_to_context=refers,
        wants_recommendations=bool(
            re.search(r"\brecommend\w*|\bactions?\b|\bwhat should\b|\bsteps\b", lower)
        ),
    )
