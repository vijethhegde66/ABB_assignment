"""Evidence analysis: focal alarm selection, likely-cause derivation and API-vs-document consistency.

Pure functions over tool results and retrieved passages - no I/O - so they
are deterministic and unit-testable.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from copilot.models import ActionItem, LikelyCause
from rag.retrieval.retriever import RetrievedChunk
from rag.text import tokenize

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------- alarm helpers
def matches_terms(alarm_name: str, terms: list[str]) -> bool:
    if not terms:
        return True
    name = alarm_name.lower()
    specific = [t for t in terms if " " in t] or terms
    return any(t in name for t in specific)


def select_focal_alarm(
    alarms: list[dict[str, Any]],
    *,
    terms: list[str] | None = None,
    prefer_active: bool = True,
    summary_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Pick the alarm the investigation should centre on.

    Order: matches the user's alarm terms > active > most recurring high/critical alarm name > severity > recency.
    """
    if not alarms:
        return None
    pool = [a for a in alarms if matches_terms(a["alarm_name"], terms or [])] or alarms
    recurring: dict[str, int] = {}
    for g in summary_groups or []:
        name = g.get("group", {}).get("alarm_name")
        if name:
            recurring[name] = int(g.get("alarm_count", 0))

    def key(a: dict[str, Any]) -> tuple[Any, ...]:
        return (
            1 if prefer_active and a.get("status") == "active" else 0,
            recurring.get(a["alarm_name"], 0) if not (prefer_active and a.get("status") == "active") else 0,
            SEVERITY_RANK.get(a.get("severity", "low"), 0),
            a.get("start_time", ""),
        )

    return max(pool, key=key)


def equipment_classes(assets: list[dict[str, Any]]) -> list[str]:
    classes: list[str] = []
    for a in assets:
        for c in [a.get("asset_type"), *[r.get("asset_type") for r in a.get("related_assets", [])]]:
            if c and c not in classes:
                classes.append(c)
    return classes


# ---------------------------------------------------------------- likely causes
@dataclass
class CauseCandidate:
    source_asset_id: str
    source_asset: str
    source_alarm: str
    targets: list[str]
    support: int
    confidence: float
    lift: float
    avg_lag: float
    upstream: bool

    def retrieval_query(self, focal_type: str) -> str:
        return (
            f"{self.source_alarm} precedes {' '.join(self.targets[:2])} {focal_type.replace('_', ' ')} cause"
        )


def cause_candidates(correlation: dict[str, Any] | None, focal_asset_ids: set[str]) -> list[CauseCandidate]:
    if not correlation:
        return []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in correlation.get("pairs", []):
        src, tgt = p["source"], p["target"]
        if tgt["asset_id"] not in focal_asset_ids:
            continue
        if src["asset_id"] == tgt["asset_id"] and src["alarm_name"] == tgt["alarm_name"]:
            continue
        groups[(src["asset_id"], src["alarm_name"])].append(p)
    # a source that is itself only ever a *consequence* of another source on the focal asset is still useful,
    # but upstream (other asset) sources are ranked first as they explain why the focal alarms occur.
    out = []
    for (aid, name), pairs in groups.items():
        best = max(pairs, key=lambda p: p["support"] * p["confidence"])
        out.append(
            CauseCandidate(
                source_asset_id=aid,
                source_asset=best["source"]["asset_name"],
                source_alarm=name,
                targets=list(dict.fromkeys(p["target"]["alarm_name"] for p in pairs)),
                support=max(p["support"] for p in pairs),
                confidence=max(p["confidence"] for p in pairs),
                lift=max(p["lift"] for p in pairs),
                avg_lag=round(sum(p["avg_lag_minutes"] for p in pairs) / len(pairs), 1),
                upstream=aid not in focal_asset_ids,
            )
        )
    # suppress pure downstream effects: if X->Y and Y is never a source to anything else, keep both,
    # but rank by (upstream, support*confidence)
    out.sort(key=lambda c: (c.upstream, c.support * c.confidence), reverse=True)
    return out[:4]


def cause_confidence(c: CauseCandidate, doc_supported: bool, wo_supported: bool) -> str:
    score = 0
    score += 2 if c.support >= 8 else 1 if c.support >= 3 else 0
    score += 1 if c.confidence >= 0.5 else 0
    score += 1 if c.lift >= 5 else 0
    score += 1 if doc_supported else 0
    score += 1 if wo_supported else 0
    return "high" if score >= 5 else "medium" if score >= 3 else "low"


def describe_cause(
    c: CauseCandidate,
    corr_ref: str,
    doc_refs: list[str],
    work_orders: list[dict[str, Any]],
    wo_ref: str | None,
) -> LikelyCause:
    targets = ", ".join(f"'{t}'" for t in c.targets[:3])
    if c.upstream:
        text = f"Upstream condition on {c.source_asset}: '{c.source_alarm}' precedes {targets}"
    else:
        text = f"Escalation on {c.source_asset}: '{c.source_alarm}' leads to {targets}"
    evidence = [
        f"Correlation: '{c.source_alarm}' preceded the focal alarms {c.support} times "
        f"(confidence {c.confidence:.0%}, lift {c.lift:g}, average lag {c.avg_lag} min)"
    ]
    refs = [corr_ref]
    wo_hits = [w for w in work_orders if w.get("asset_id") == c.source_asset_id]
    for w in wo_hits[:2]:
        evidence.append(
            f"Open work order {w['work_order_id']} on {c.source_asset}: {w['title']} ({w['status']})"
        )
    if wo_hits and wo_ref:
        refs.append(wo_ref)
    if doc_refs:
        evidence.append("Documented failure mode in troubleshooting / maintenance guidance")
        refs.extend(doc_refs)
    return LikelyCause(
        cause=text,
        evidence=evidence,
        confidence=cause_confidence(c, bool(doc_refs), bool(wo_hits)),  # type: ignore[arg-type]
        refs=list(dict.fromkeys(refs)),
    )


# ---------------------------------------------------------------- consistency
_PROHIBITIVE = (
    r"\b(do not|don't|shall not|must not|never|not (?:be )?(?:attempt|allowed|acceptable|permitted))\b"
)
CONFLICT_RULES: list[tuple[re.Pattern[str], re.Pattern[str], str]] = [
    (
        re.compile(r"\b(restart|reset|re-?start)\b", re.I),
        re.compile(_PROHIBITIVE + r"[^.]{0,120}\b(restart|reset)", re.I),
        "the document prohibits resetting / restarting before the trip cause is identified",
    ),
    (
        re.compile(r"\bcontinue (operation|operating|running)\b|\bmonitor\b.{0,60}\bfor up to\b", re.I),
        re.compile(
            r"(not acceptable|do not continue|shall not continue|must not continue|shall be changed over|"
            r"do not continue extended operation)",
            re.I,
        ),
        "the document limits continued operation above the alarm limit and requires changeover / approval",
    ),
    (
        re.compile(r"\b(bypass|disable|inhibit|suppress|shelve)\b", re.I),
        re.compile(_PROHIBITIVE + r"[^.]{0,80}\b(bypass|disabl|inhibit|defeat|suppress|shelv)", re.I),
        "the document prohibits defeating protections or disabling alarms",
    ),
]


@dataclass
class ConsistencyResult:
    status: str  # supported | conflict | not_covered
    refs: list[str]
    note: str


def _content(text: str) -> set[str]:
    return {t for t in tokenize(text) if len(t) > 2}


def check_recommendation(
    action: str, reference_procedure: str | None, passages: list[tuple[str, RetrievedChunk]]
) -> ConsistencyResult:
    """Compare one API recommendation against retrieved controlled passages ``[(citation_id, chunk)]``."""
    act = _content(action)
    best: tuple[float, str, RetrievedChunk] | None = None
    for cid, chunk in passages:
        if chunk.metadata.get("trust_level") != "controlled":
            continue
        overlap = len(act & _content(chunk.text)) / max(1, len(act))
        for api_re, doc_re, why in CONFLICT_RULES:
            if api_re.search(action) and doc_re.search(chunk.text) and overlap >= 0.15:
                return ConsistencyResult("conflict", [cid], f"Conflicts with {chunk.doc_id}: {why}.")
        if best is None or overlap > best[0]:
            best = (overlap, cid, chunk)
    if best is None:
        return ConsistencyResult(
            "not_covered", [], "No controlled document passage retrieved for this action."
        )
    overlap, cid, chunk = best
    same_doc = reference_procedure is not None and reference_procedure == chunk.doc_id
    if overlap >= 0.45 or (same_doc and overlap >= 0.25):
        return ConsistencyResult(
            "supported", [cid], f"Consistent with {chunk.doc_id} ({chunk.metadata.get('heading')})."
        )
    if overlap >= 0.3:
        return ConsistencyResult("supported", [cid], f"Broadly consistent with {chunk.doc_id}.")
    return ConsistencyResult("not_covered", [], "Not explicitly covered by the retrieved procedures/manuals.")


# ---------------------------------------------------------------- procedure steps
_STEP = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|-)\s+(.{12,})$", re.M)


def procedure_steps(text: str, limit: int = 5) -> list[str]:
    steps = [m.group(1).strip() for m in _STEP.finditer(text)]
    return steps[:limit]


def build_actions(
    procedure: tuple[str, RetrievedChunk] | None,
    api_recs: list[dict[str, Any]],
    rec_checks: list[ConsistencyResult],
    rec_ref: str | None,
    investigation_actions: list[ActionItem],
) -> list[ActionItem]:
    actions: list[ActionItem] = []
    if procedure is not None:
        cid, chunk = procedure
        for step in procedure_steps(chunk.text):
            actions.append(
                ActionItem(
                    action=step,
                    source="procedure",
                    urgency="immediate",
                    refs=[cid],
                    note=f"{chunk.doc_id} {chunk.metadata.get('heading', '')}".strip(),
                )
            )
    for rec, check in zip(api_recs, rec_checks, strict=True):
        refs = ([rec_ref] if rec_ref else []) + check.refs
        actions.append(
            ActionItem(
                action=rec["action"],
                source="api",
                urgency=rec.get("urgency"),
                consistency=check.status,  # type: ignore[arg-type]
                note=check.note,
                refs=refs,
            )
        )  # type: ignore[arg-type]
    actions.extend(investigation_actions)
    return actions
