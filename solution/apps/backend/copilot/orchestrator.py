"""Copilot orchestration: NL request -> intent -> MCP tool chain + RAG -> evidence fusion -> grounded answer.

The orchestrator is a *stage pipeline* whose stages are selected by intent and
whose arguments are derived from previous tool outputs (asset ids from
search_assets feed get_asset_metadata, related asset ids feed correlation,
the focal alarm id feeds priority scoring and recommendations, alarm names
feed retrieval queries ...). Independent calls within a stage run
concurrently. Every failure is contained: the stage records a warning,
the answer is marked ``partial`` and still grounded in what succeeded.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from copilot.composer import compose_markdown, evidence_pack, llm_answer
from copilot.evidence import (
    CauseCandidate,
    ConsistencyResult,
    build_actions,
    cause_candidates,
    check_recommendation,
    describe_cause,
    equipment_classes,
    matches_terms,
    select_focal_alarm,
)
from copilot.intent import Understanding, understand
from copilot.llm import LLMProvider
from copilot.mcp_client import McpSession, McpToolRegistry
from copilot.memory import ConversationState, ConversationStore
from copilot.models import (
    ActionItem,
    Answer,
    ChatResponse,
    IntentInfo,
    Investigation,
    LikelyCause,
    ProposedAction,
    RetrievalTrace,
)
from copilot.rag_service import CitationRegistry, RagService, RagUnavailableError
from platform_common import TraceContext, get_logger, new_id, trace_scope
from rag.retrieval.retriever import RetrievalFilters, RetrievalResult, RetrievedChunk

log = get_logger("copilot.orchestrator")

ALARM_SERVER = "alarm-management"
NEEDS_SCOPE = {"investigate", "active_response", "priority_ranking", "related_assets", "alarm_performance"}
NEEDS_FOCAL = {"active_response", "related_assets", "procedure_lookup", "consistency_check", "investigate"}
PROCEDURE_TYPES = ["operating_procedure", "safety_instruction"]
GUIDANCE_TYPES = ["troubleshooting_guide", "maintenance_manual", "resolution_notes"]
ALL_CONTROLLED = [
    "operating_procedure",
    "safety_instruction",
    "maintenance_manual",
    "troubleshooting_guide",
    "resolution_notes",
    "alarm_philosophy",
]


@dataclass
class Turn:
    u: Understanding
    state: ConversationState
    trace: TraceContext
    mcp: McpSession
    cites: CitationRegistry = field(default_factory=CitationRegistry)
    inv: Investigation = field(default_factory=Investigation)
    refs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    retrieval: list[RetrievalTrace] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    clarification: str | None = None
    degraded: bool = False
    window: dict[str, str] = field(default_factory=dict)
    lookback: int = 90
    proposed: list[ProposedAction] = field(default_factory=list)
    rag_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def asset_ids(self) -> list[str]:
        return [a["asset_id"] for a in self.inv.assets]


class Orchestrator:
    def __init__(
        self,
        registry: McpToolRegistry,
        rag: RagService,
        store: ConversationStore,
        llm: LLMProvider | None = None,
        *,
        default_lookback_days: int = 90,
        reference_time: datetime | None = None,
    ) -> None:
        self.registry = registry
        self.rag = rag
        self.store = store
        self.llm = llm
        self.default_lookback = default_lookback_days
        self.reference_time = reference_time

    # ================================================================== entry point
    async def chat(self, message: str, conversation_id: str | None = None) -> ChatResponse:
        t0 = time.perf_counter()
        conversation_id = conversation_id or new_id("conv")
        trace = TraceContext(trace_id=new_id("trace"), conversation_id=conversation_id)
        state = self.store.get(conversation_id)
        u = understand(message)
        with trace_scope(trace):
            log.info("chat_turn", extra={"intent": u.intent, "intent_confidence": u.confidence})
            async with self.registry.session() as mcp:
                turn = Turn(u=u, state=state, trace=trace, mcp=mcp)
                turn.lookback = (
                    u.lookback_days
                    or (state.lookback_days if u.refers_to_context else None)
                    or self.default_lookback
                )
                end = (self.reference_time or datetime.now(UTC)).replace(microsecond=0)
                turn.window = {"start_time": _iso(end - timedelta(days=turn.lookback)), "end_time": _iso(end)}
                try:
                    await self._run(turn)
                except Exception:
                    log.exception("orchestration_failed")
                    turn.warnings.append(
                        f"Unexpected orchestration error (trace {trace.trace_id}); partial results are shown."
                    )
                    turn.degraded = True
                response = await self._respond(turn, t0)
        self._remember(turn, response)
        return response

    # ================================================================== pipeline
    async def _run(self, t: Turn) -> None:
        u = t.u
        alarm_ok = t.mcp.status.get(ALARM_SERVER) and t.mcp.status[ALARM_SERVER].connected
        if not alarm_ok:
            status = t.mcp.status.get(ALARM_SERVER)
            t.warnings.append(
                "Alarm Management MCP server is unavailable"
                + (f" ({status.error})" if status and status.error else "")
                + " - live alarm data could not be retrieved; answer is based on documents only."
            )
            t.degraded = True
            await self._rag_stage(t)
            return

        intent = u.intent
        if intent == "document_question" or (intent == "procedure_lookup" and not self._has_any_scope(t)):
            await self._rag_stage(t)
            return

        if not await self._resolve_scope(t):
            return
        await self._asset_metadata(t)

        if intent == "alarm_performance":
            await self._performance(t)
        elif intent == "priority_ranking":
            await self._active_alarms(t)
            await self._rank(t)
            if t.inv.focal_alarm:
                await self._recommend(t)
        else:
            # history + active alarms in parallel
            jobs = []
            if intent in {"investigate", "related_assets"} or u.severities or u.lookback_days:
                jobs.append(self._history(t))
            if (
                intent
                in {
                    "investigate",
                    "active_response",
                    "related_assets",
                    "procedure_lookup",
                    "consistency_check",
                }
                or u.status == "active"
            ):
                jobs.append(self._active_alarms(t))
            await asyncio.gather(*jobs)
            await self._focal(t)
            # focal-dependent analytics in parallel
            jobs = []
            if t.inv.assets and intent in {"investigate", "related_assets"}:
                jobs.append(self._correlate(t))
            if t.inv.focal_alarm:
                if intent == "active_response" and len(t.inv.active_alarms) > 1:
                    jobs.append(self._rank(t, from_active=True))
                else:
                    jobs.append(self._score_focal(t))
                jobs.append(self._recommend(t))
            await asyncio.gather(*jobs)
        await self._work_orders(t)
        await self._rag_stage(t)
        self._finalise_investigation(t)

    def _has_any_scope(self, t: Turn) -> bool:
        u = t.u
        return bool(
            u.asset_ids
            or u.asset_phrases
            or u.alarm_id
            or u.site
            or u.unit
            or (u.refers_to_context and (t.state.assets or t.state.focal_alarm))
        )

    # ------------------------------------------------------------------ tool call helper
    async def _call(
        self,
        t: Turn,
        step: str,
        tool: str,
        args: dict[str, Any],
        role: str | None = None,
        *,
        required: bool = True,
    ) -> dict[str, Any] | None:
        out = await t.mcp.call(tool, args, step=step, trace=t.trace)
        if role:
            t.refs.setdefault(role, out.record.ref)
        if out.ok:
            return out.data
        err = out.record.error or {}
        code = err.get("error_code", "ERROR")
        human = {
            "TOOL_UNAVAILABLE": f"tool '{tool}' is not available on the connected MCP servers",
            "UPSTREAM_UNAVAILABLE": "the Alarm Management API is unavailable (retries exhausted)",
            "UPSTREAM_TIMEOUT": "the Alarm Management API timed out (retries exhausted)",
            "MCP_TIMEOUT": "the MCP server did not respond in time",
            "INVALID_ARGUMENT": "the request was rejected as invalid",
            "NOT_FOUND": "the requested record was not found",
            "AUTHENTICATION_FAILED": "authentication to the Alarm Management API failed",
        }.get(code, err.get("message", "unknown error"))
        t.warnings.append(f"{step}: `{tool}` failed - {human} [{out.record.ref}, {code}].")
        if required:
            t.degraded = True
        return None

    # ------------------------------------------------------------------ stage: scope resolution
    async def _resolve_scope(self, t: Turn) -> bool:
        u, st = t.u, t.state
        assets: list[dict[str, Any]] = []
        if u.asset_ids:
            assets = [{"asset_id": a} for a in u.asset_ids]
        elif u.asset_phrases:
            results = await asyncio.gather(
                *(
                    self._call(
                        t,
                        "resolve_asset",
                        "search_assets",
                        {"query": p, "site": u.site, "unit": u.unit, "limit": 10},
                        "asset_search",
                    )
                    for p in u.asset_phrases
                )
            )
            for phrase, res in zip(u.asset_phrases, results, strict=True):
                if not res:
                    continue
                matches = res.get("results", [])
                if u.generic_asset:
                    cls = phrase.lower().replace(" ", "_")
                    typed = [m for m in matches if cls in m["asset_type"]] or matches
                    assets.extend(typed[:3])
                elif res.get("best_match") and res["best_match"]["match_score"] >= 0.45:
                    assets.append(res["best_match"])
                    if res.get("ambiguous"):
                        alts = ", ".join(m["asset_name"] for m in matches[1:3])
                        t.warnings.append(
                            f"'{phrase}' is ambiguous; using {res['best_match']['asset_name']} "
                            f"(other candidates: {alts})."
                        )
            if not assets:
                tried = ", ".join(f"'{p}'" for p in u.asset_phrases)
                t.clarification = (
                    f"I could not find an asset matching {tried} in the Alarm Management system. "
                    "Please give the asset name or tag as shown in the DCS (for example "
                    "'Boiler Feed Pump 101' or 'K-201')."
                )
                return False
        elif u.alarm_id:
            detail = await self._call(
                t, "resolve_alarm", "get_alarm_details", {"alarm_id": u.alarm_id}, "focal"
            )
            if not detail:
                t.clarification = f"Alarm {u.alarm_id} was not found."
                return False
            t.inv.focal_alarm = detail["alarm"]
            assets = [{"asset_id": detail["alarm"]["asset_id"]}]
        elif u.site or u.unit:
            t.inv.scope = {k: v for k, v in {"site": u.site, "unit": u.unit}.items() if v}
            return True
        elif st.focal_alarm and (
            u.refers_to_context or u.intent in {"procedure_lookup", "consistency_check", "related_assets"}
        ):
            assets = [{"asset_id": st.focal_alarm["asset_id"]}]
            t.notes.append(
                f"Using the alarm from the conversation context: {st.focal_alarm['alarm_name']} on "
                f"{st.focal_alarm.get('asset_name', st.focal_alarm['asset_id'])}."
            )
        elif st.assets:
            assets = [dict(a) for a in st.assets]
            t.notes.append(
                f"Using the asset from the conversation context: "
                f"{', '.join(a['asset_name'] for a in st.assets if 'asset_name' in a)}."
            )
        elif st.site or st.unit:
            t.inv.scope = {k: v for k, v in {"site": st.site, "unit": st.unit}.items() if v}
            return True
        else:
            if t.u.intent in NEEDS_SCOPE or t.u.intent in NEEDS_FOCAL:
                t.clarification = (
                    "Which asset, unit or site should I look at? For example: 'Investigate "
                    "recurring alarms on Boiler Feed Pump 101 over the last 90 days'."
                )
                return False
        # de-duplicate, keep order
        seen: set[str] = set()
        t.inv.assets = [a for a in assets if not (a["asset_id"] in seen or seen.add(a["asset_id"]))]  # type: ignore[func-returns-value]
        t.inv.scope = {"asset_ids": t.asset_ids}
        return True

    async def _asset_metadata(self, t: Turn) -> None:
        if not t.inv.assets:
            return
        metas = await asyncio.gather(
            *(
                self._call(
                    t,
                    "asset_context",
                    "get_asset_metadata",
                    {"asset_id": a["asset_id"]},
                    "metadata",
                    required=False,
                )
                for a in t.inv.assets
            )
        )
        enriched = []
        for a, m in zip(t.inv.assets, metas, strict=True):
            if m:
                enriched.append({k: v for k, v in m.items() if k != "trace"})
            else:
                enriched.append(a)
        t.inv.assets = enriched
        sites = {a.get("site") for a in enriched if a.get("site")}
        units = {a.get("unit") for a in enriched if a.get("unit")}
        t.inv.scope.update(
            {
                "site": next(iter(sites)) if len(sites) == 1 else None,
                "unit": next(iter(units)) if len(units) == 1 else None,
            }
        )

    # ------------------------------------------------------------------ stage: alarms
    def _scope_args(self, t: Turn) -> list[dict[str, Any]]:
        if t.inv.assets:
            return [{"asset_id": a} for a in t.asset_ids]
        return [
            {k: v for k, v in {"site": t.inv.scope.get("site"), "unit": t.inv.scope.get("unit")}.items() if v}
        ]

    async def _history(self, t: Turn) -> None:
        u = t.u
        scope = (
            {"asset_ids": t.asset_ids}
            if t.inv.assets
            else {
                k: v
                for k, v in {"site": t.inv.scope.get("site"), "unit": t.inv.scope.get("unit")}.items()
                if v
            }
        )
        common = {**scope, "severity": u.severities, **t.window}
        summary_job = self._call(
            t,
            "alarm_history",
            "summarize_alarms",
            {
                **common,
                "group_by": ["alarm_name"],
                "kpis": ["alarm_count", "recurring_rate", "avg_ack_delay", "critical_count"],
            },
            "summary",
        )
        trend_job = self._call(
            t,
            "alarm_history",
            "get_alarm_trends",
            {**common, "bucket": "weekly", "metrics": ["alarm_count"]},
            "trend",
            required=False,
        )
        list_jobs = [
            self._call(
                t,
                "alarm_history",
                "get_alarms",
                {
                    **s,
                    "severity": u.severities,
                    **t.window,
                    "page_size": 200,
                    "fetch_all_pages": True,
                    "max_pages": 3,
                },
                "alarms",
                required=False,
            )
            for s in self._scope_args(t)
        ]
        summary, trend, *lists = await asyncio.gather(summary_job, trend_job, *list_jobs)
        if summary:
            groups = summary.get("groups", [])
            t.inv.alarm_summary = {
                "total_alarms": summary["total_alarms"],
                "kpis": summary["kpis"],
                "severity_filter": u.severities,
                "top_alarms": [
                    {
                        "alarm_name": g["group"].get("alarm_name"),
                        "alarm_count": g["alarm_count"],
                        "recurring_rate": g.get("recurring_rate"),
                        "max_severity": g.get("max_severity"),
                        "last_occurrence": g.get("last_occurrence"),
                    }
                    for g in groups[:8]
                ],
            }
            t.data["summary_groups"] = groups
        if trend:
            t.inv.trend = trend["trend"]
        t.data["history_alarms"] = [a for lst in lists if lst for a in lst.get("alarms", [])]
        t.inv.time_window = t.window

    async def _active_alarms(self, t: Turn) -> None:
        results = await asyncio.gather(
            *(
                self._call(
                    t,
                    "active_alarms",
                    "get_alarms",
                    {**s, "status": "active", "sort_by": "severity", "sort_order": "desc", "page_size": 100},
                    "active",
                    required=t.u.intent in {"active_response", "priority_ranking"},
                )
                for s in self._scope_args(t)
            )
        )
        rows = [a for r in results if r for a in r.get("alarms", [])]
        if t.u.severities and t.u.intent == "active_response":
            filtered = [a for a in rows if a["severity"] in t.u.severities]
            if not filtered and rows:
                t.warnings.append(f"No active {'/'.join(t.u.severities)} alarms; showing all active alarms.")
            rows = filtered or rows
        rows.sort(
            key=lambda a: ({"critical": 3, "high": 2, "medium": 1}.get(a["severity"], 0), a["start_time"]),
            reverse=True,
        )
        t.inv.active_alarms = rows

    async def _focal(self, t: Turn) -> None:
        u, st = t.u, t.state
        if t.inv.focal_alarm:
            return
        ctx_alarm = st.focal_alarm
        same_asset = ctx_alarm and (not t.inv.assets or ctx_alarm.get("asset_id") in t.asset_ids)
        if (
            u.refers_to_context
            and ctx_alarm
            and same_asset
            and (not u.alarm_terms or matches_terms(ctx_alarm["alarm_name"], u.alarm_terms))
        ):
            detail = await self._call(
                t,
                "focal_alarm",
                "get_alarm_details",
                {"alarm_id": ctx_alarm["alarm_id"]},
                "focal",
                required=False,
            )
            t.inv.focal_alarm = detail["alarm"] if detail else ctx_alarm
            return
        pool = t.inv.active_alarms + t.data.get("history_alarms", [])
        if not pool and t.inv.assets:
            # nothing in the requested window: look at the latest alarm on the asset(s)
            res = await self._call(
                t,
                "focal_alarm",
                "get_alarms",
                {**self._scope_args(t)[0], "page_size": 50, "lookback_days": 365},
                "alarms",
                required=False,
            )
            pool = res.get("alarms", []) if res else []
        if u.alarm_terms and pool and not any(matches_terms(a["alarm_name"], u.alarm_terms) for a in pool):
            t.warnings.append(
                f"No alarm matching '{', '.join(u.alarm_terms)}' was found in the window; "
                "the investigation uses the most significant alarm instead."
            )
        prefer_active = u.intent != "investigate" or u.status == "active"
        t.inv.focal_alarm = select_focal_alarm(
            pool,
            terms=u.alarm_terms,
            prefer_active=prefer_active,
            summary_groups=t.data.get("summary_groups"),
        )
        if t.inv.focal_alarm:
            t.refs.setdefault("focal", t.refs.get("active") or t.refs.get("alarms") or "")
        elif u.intent in NEEDS_FOCAL:
            t.warnings.append("No alarms were found for the selected scope and window.")

    # ------------------------------------------------------------------ stage: analytics
    async def _correlate(self, t: Turn) -> None:
        focal_assets = t.asset_ids[:3]
        if t.inv.focal_alarm and t.inv.focal_alarm["asset_id"] not in focal_assets:
            focal_assets.insert(0, t.inv.focal_alarm["asset_id"])
        res = await self._call(
            t,
            "root_cause_analysis",
            "correlate_alarms",
            {
                "asset_ids": focal_assets,
                "include_related": True,
                **t.window,
                "severity_threshold": "medium",
                "min_support": 2,
                "lag_window_minutes": 15,
            },
            "correlation",
            required=False,
        )
        if res:
            t.data["correlation"] = res
            t.inv.correlations = res.get("pairs", [])[:10]

    async def _score_focal(self, t: Turn) -> None:
        assert t.inv.focal_alarm
        res = await self._call(
            t,
            "prioritisation",
            "score_alarm_priority",
            {"alarm_id": t.inv.focal_alarm["alarm_id"]},
            "priority",
            required=False,
        )
        if res:
            t.inv.priority = {k: v for k, v in res.items() if k != "trace"}

    async def _rank(self, t: Turn, from_active: bool = False) -> None:
        candidates = t.inv.active_alarms
        if not candidates and not from_active:
            # no active alarms: rank the most recent high/critical alarms of the last 7 days
            scope = self._scope_args(t)[0]
            res = await self._call(
                t,
                "prioritisation",
                "get_alarms",
                {**scope, "severity": ["high", "critical"], "lookback_days": 7, "page_size": 50},
                "alarms",
                required=False,
            )
            candidates = res.get("alarms", []) if res else []
            if candidates:
                t.warnings.append(
                    "No active alarms in scope; ranked high/critical alarms of the last 7 days."
                )
        if not candidates:
            t.warnings.append("No alarms available to rank in the selected scope.")
            return
        ids = [a["alarm_id"] for a in candidates[:20]]
        res = await self._call(t, "prioritisation", "rank_alarm_priorities", {"alarm_ids": ids}, "ranking")
        if not res:
            # degraded: fall back to severity ordering from the API data
            t.inv.focal_alarm = t.inv.focal_alarm or candidates[0]
            return
        t.inv.ranked_alarms = res["ranked"]
        for f in res.get("failed", []):
            t.warnings.append(f"Priority score unavailable for {f['alarm_id']} ({f.get('error_code')}).")
        if res["ranked"]:
            top_id = res["ranked"][0]["alarm_id"]
            t.inv.focal_alarm = next((a for a in candidates if a["alarm_id"] == top_id), t.inv.focal_alarm)
            t.refs["focal"] = t.refs["ranking"]
            t.inv.priority = {**res["ranked"][0], "target_response_time": _band_response(res["ranked"][0])}

    async def _recommend(self, t: Turn) -> None:
        assert t.inv.focal_alarm
        res = await self._call(
            t,
            "operator_guidance",
            "recommend_operator_actions",
            {
                "alarm_id": t.inv.focal_alarm["alarm_id"],
                "include_related": True,
                "include_asset_context": True,
                "include_historical_pattern": True,
            },
            "recommendations",
            required=False,
        )
        if res:
            t.inv.api_recommendations = res.get("recommendations", [])
            t.data["recommendation"] = res

    async def _performance(self, t: Turn) -> None:
        scope = {
            k: v for k, v in {"site": t.inv.scope.get("site"), "unit": t.inv.scope.get("unit")}.items() if v
        }
        asset_scope = {"asset_ids": t.asset_ids} if t.inv.assets and not scope else {}
        kpi_types = [
            "alarm_flood_index",
            "nuisance_alarm_score",
            "operator_response_efficiency",
            "alarm_rate_per_hour",
        ]
        jobs = [
            self._call(
                t,
                "kpi",
                "calculate_kpi",
                {"calculation_type": k, **scope, **asset_scope, **t.window},
                f"kpi_{k}",
                required=False,
            )
            for k in kpi_types
        ]
        if scope:
            jobs.append(
                self._call(t, "kpi", "analyze_alarm_floods", {**scope, **t.window}, "floods", required=False)
            )
        jobs.append(
            self._call(
                t,
                "kpi",
                "find_rationalization_candidates",
                {**scope, **asset_scope, **t.window},
                "rationalization",
                required=False,
            )
        )
        results = await asyncio.gather(*jobs)
        for k, r in zip(kpi_types, results[: len(kpi_types)], strict=True):
            if r:
                t.inv.kpis.append(
                    {
                        "name": k.replace("_", " "),
                        "value": r["result"]["value"],
                        "unit": r["result"]["unit"],
                        "benchmark": r.get("benchmark"),
                        "ref": t.refs.get(f"kpi_{k}"),
                    }
                )
        rest = results[len(kpi_types) :]
        floods = rest[0] if scope and rest else None
        rat = rest[-1] if rest else None
        if floods:
            t.inv.kpis.append(
                {
                    "name": "flood periods",
                    "value": floods["flood_count"],
                    "unit": "floods",
                    "benchmark": "< 1% of time in flood",
                    "ref": t.refs.get("floods"),
                }
            )
        if rat:
            t.data["rationalization"] = rat
            for c in rat.get("candidates", [])[:5]:
                t.inv.actions.append(
                    ActionItem(
                        action=f"Rationalize '{c['alarm_name']}' on {c['asset_name']} ({', '.join(c['reasons'])}, "
                        f"{c['occurrences']} occurrences): {' '.join(c['recommendations'][:1])}",
                        source="investigation",
                        refs=[t.refs.get("rationalization", "")],
                    )
                )
        t.inv.time_window = t.window

    async def _work_orders(self, t: Turn) -> None:
        if not t.mcp.has("list_work_orders") or not t.inv.assets:
            return
        ids = list(t.asset_ids)
        for a in t.inv.assets:
            ids.extend(r["asset_id"] for r in a.get("related_assets", []))
        res = await self._call(
            t,
            "work_orders",
            "list_work_orders",
            {"asset_ids": list(dict.fromkeys(ids))[:20]},
            "work_orders",
            required=False,
        )
        if res:
            t.inv.work_orders = res.get("work_orders", [])

    # ------------------------------------------------------------------ stage: RAG
    async def _search(
        self, t: Turn, purpose: str, query: str, filters: RetrievalFilters, top_k: int = 3
    ) -> RetrievalResult | None:
        try:
            result, trace = await self.rag.search(purpose, query, filters, top_k)
        except RagUnavailableError as exc:
            if not any("retrieval index" in w for w in t.warnings):
                t.warnings.append(f"Document retrieval unavailable: {exc}.")
            t.degraded = True
            return None
        t.retrieval.append(trace)
        for q in result.quarantined:
            note = (
                f"Passage {q['chunk_id']} was excluded from grounding: suspected prompt-injection content "
                f"({', '.join(q['signals'])})."
            )
            if note not in t.rag_notes:
                t.rag_notes.append(note)
        return result

    async def _rag_stage(self, t: Turn) -> None:
        u, inv = t.u, t.inv
        classes = equipment_classes(inv.assets) or None
        focal = inv.focal_alarm
        focal_type = (focal or {}).get("asset_type") or (
            inv.assets[0]["asset_type"] if inv.assets and inv.assets[0].get("asset_type") else ""
        )
        jobs: dict[str, Any] = {}
        sites = [
            x
            for x in {(focal or {}).get("site"), inv.scope.get("site"), *[a.get("site") for a in inv.assets]}
            if x
        ] or None

        if u.intent == "document_question" or not (focal or inv.assets or inv.scope):
            jobs["answer"] = self._search(
                t, "question", u.text, RetrievalFilters(equipment_classes=classes), 4
            )
        if focal:
            focal_classes = list(
                dict.fromkeys([c for c in [focal.get("asset_type"), focal_type] if c] + (classes or []))
            )
            jobs["procedure"] = self._search(
                t,
                "applicable_procedure",
                f"{focal['alarm_name']} alarm response procedure {focal_type.replace('_', ' ')}",
                RetrievalFilters(
                    doc_types=PROCEDURE_TYPES,
                    equipment_classes=focal_classes or None,
                    alarm_codes=[focal["alarm_code"]],
                    sites=sites,
                ),
                3,
            )
        if u.intent in {"investigate", "related_assets"} and (inv.alarm_summary or focal):
            names = [a["alarm_name"] for a in (inv.alarm_summary or {}).get("top_alarms", [])[:2]] or (
                [focal["alarm_name"]] if focal else []
            )
            jobs["guidance"] = self._search(
                t,
                "troubleshooting",
                f"recurring {' '.join(names)} likely causes investigation {focal_type.replace('_', ' ')}",
                RetrievalFilters(doc_types=GUIDANCE_TYPES, equipment_classes=classes, sites=sites),
                3,
            )
        if u.intent == "related_assets" and focal:
            jobs["related"] = self._search(
                t,
                "related_equipment",
                f"related equipment to inspect after {focal['alarm_name']}",
                RetrievalFilters(
                    doc_types=PROCEDURE_TYPES + GUIDANCE_TYPES, equipment_classes=classes, sites=sites
                ),
                3,
            )
        if u.intent == "priority_ranking":
            jobs["philosophy"] = self._search(
                t,
                "priority_policy",
                "alarm priority order response time criticality",
                RetrievalFilters(doc_types=["alarm_philosophy"]),
                2,
            )
        if u.intent == "alarm_performance":
            jobs["philosophy"] = self._search(
                t,
                "performance_targets",
                "alarm flood chattering performance targets rationalisation deadband",
                RetrievalFilters(doc_types=["alarm_philosophy"]),
                3,
            )

        candidates: list[CauseCandidate] = []
        if t.data.get("correlation"):
            focal_ids = set(t.asset_ids) | ({focal["asset_id"]} if focal else set())
            candidates = cause_candidates(t.data["correlation"], focal_ids)
            for i, c in enumerate(candidates[:3]):
                jobs[f"cause_{i}"] = self._search(
                    t,
                    "cause_evidence",
                    c.retrieval_query(focal_type),
                    RetrievalFilters(doc_types=GUIDANCE_TYPES, equipment_classes=classes, sites=sites),
                    2,
                )
        for i, rec in enumerate(inv.api_recommendations[:6]):
            jobs[f"rec_{i}"] = self._search(
                t,
                "recommendation_check",
                f"{rec['action']} {(focal or {}).get('alarm_name', '')}",
                RetrievalFilters(
                    doc_types=ALL_CONTROLLED,
                    equipment_classes=classes,
                    sites=sites,
                    alarm_codes=[focal["alarm_code"]] if focal else None,
                ),
                3,
            )

        keys = list(jobs)
        results: dict[str, RetrievalResult | None] = dict(
            zip(keys, await asyncio.gather(*jobs.values()), strict=True)
        )

        def register(
            key: str, min_conf: tuple[str, ...] = ("high", "medium")
        ) -> list[tuple[str, RetrievedChunk]]:
            r = results.get(key)
            if not r or r.confidence not in min_conf:
                return []
            ids = t.cites.add(r.hits)
            return list(zip(ids, r.hits, strict=True))

        if "answer" in results:
            r = results["answer"]
            if r is None or r.confidence in {"none", "low"}:
                t.rag_notes.append(
                    "No document passage answers this question with sufficient confidence; "
                    "please consult the document owner or rephrase."
                )
                if r and r.confidence == "low":
                    register("answer", ("low",))
            else:
                ids = [cid for cid, _ in register("answer")]
                inv.procedures.extend(ids[:3])
        proc_hits = register("procedure")
        if focal and not proc_hits:
            t.rag_notes.append(
                f"No controlled operating procedure matched '{focal['alarm_name']}' with sufficient "
                "confidence - follow the general alarm response and consult the shift supervisor."
            )
        inv.procedures.extend(cid for cid, _ in proc_hits)
        guidance_hits = register("guidance")
        inv.procedures.extend(cid for cid, _ in guidance_hits[:2])
        related_hits = register("related")
        inv.procedures.extend(cid for cid, _ in related_hits[:1])
        philosophy_hits = register("philosophy")
        if philosophy_hits:
            t.refs["philosophy"] = philosophy_hits[0][0]
            inv.procedures.extend(cid for cid, _ in philosophy_hits[:1])
        inv.procedures = list(dict.fromkeys(inv.procedures))

        # ---- likely causes (MCP correlation + RAG evidence + work orders)
        for i, c in enumerate(candidates[:3]):
            hits = register(f"cause_{i}")
            doc_refs = [cid for cid, h in hits if h.metadata.get("doc_type") in GUIDANCE_TYPES][:2]
            inv.likely_causes.append(
                describe_cause(
                    c, t.refs.get("correlation", ""), doc_refs, inv.work_orders, t.refs.get("work_orders")
                )
            )
        if inv.trend and inv.trend.get("direction") == "increasing":
            inv.likely_causes.append(
                LikelyCause(
                    cause="The alarm frequency is increasing over the window - the underlying condition is "
                    "deteriorating rather than random",
                    evidence=[
                        f"{inv.trend['first_half_count']} alarms in the first half vs "
                        f"{inv.trend['second_half_count']} in the second half"
                    ],
                    confidence="medium",
                    refs=[t.refs.get("trend", "")],
                )
            )
        if u.intent == "investigate" and not t.data.get("correlation") and inv.assets:
            t.warnings.append(
                "Correlation analysis unavailable - contributing factors are based on alarm history "
                "and documents only."
            )
            for cid, h in guidance_hits[:1]:
                inv.likely_causes.append(
                    LikelyCause(
                        cause=f"Documented common causes for these alarms ({h.metadata.get('heading')})",
                        evidence=["From troubleshooting guidance; not confirmed by live correlation"],
                        confidence="low",
                        refs=[cid],
                    )
                )

        # ---- consistency of API recommendations with documents
        checks: list[ConsistencyResult] = []
        for i, rec in enumerate(inv.api_recommendations[:6]):
            passages = register(f"rec_{i}") + proc_hits
            checks.append(check_recommendation(rec["action"], rec.get("reference_procedure"), passages))
        inv.api_recommendations = inv.api_recommendations[:6]

        follow_up = self._investigation_actions(t, candidates, proc_hits + guidance_hits)
        inv.actions = build_actions(
            proc_hits[0] if proc_hits else None,
            inv.api_recommendations,
            checks,
            t.refs.get("recommendations"),
            follow_up,
        ) + [a for a in inv.actions if a.source == "investigation"]

    def _investigation_actions(
        self, t: Turn, candidates: list[CauseCandidate], doc_hits: list[tuple[str, RetrievedChunk]]
    ) -> list[ActionItem]:
        out: list[ActionItem] = []
        inv = t.inv
        for c in candidates[:3]:
            wo = next((w for w in inv.work_orders if w["asset_id"] == c.source_asset_id), None)
            if wo:
                out.append(
                    ActionItem(
                        action=f"Expedite {wo['work_order_id']} on {c.source_asset}: {wo['title']}",
                        source="investigation",
                        refs=[t.refs.get("work_orders", "")],
                    )
                )
            elif c.upstream:
                out.append(
                    ActionItem(
                        action=f"Inspect {c.source_asset}: '{c.source_alarm}' is a leading indicator "
                        f"of the focal alarms",
                        source="investigation",
                        refs=[t.refs.get("correlation", "")],
                    )
                )
        recurring = [
            s
            for s in (inv.alarm_summary or {}).get("top_alarms", [])
            if (s.get("recurring_rate") or 0) > 0.15 and s["alarm_count"] >= 4
        ]
        if recurring and t.u.intent == "investigate":
            rec_doc = next(
                (cid for cid, h in doc_hits if "recurr" in (h.metadata.get("heading") or "").lower()), None
            )
            out.append(
                ActionItem(
                    action="Raise a work request and a reliability investigation for the recurring alarms "
                    f"({', '.join(r['alarm_name'] for r in recurring[:2])})",
                    source="investigation",
                    refs=[t.refs.get("summary", ""), *([rec_doc] if rec_doc else [])],
                )
            )
            target = next((c for c in candidates if c.upstream), None)
            asset = (target.source_asset_id if target else inv.assets[0]["asset_id"]) if inv.assets else None
            if asset and not any(w["asset_id"] == asset for w in inv.work_orders):
                name = target.source_asset if target else inv.assets[0].get("asset_name", asset)
                self._propose(
                    t,
                    asset,
                    f"Investigate recurring alarms - {name}",
                    f"Recurring {', '.join(r['alarm_name'] for r in recurring[:2])} on "
                    f"{', '.join(a.get('asset_name', a['asset_id']) for a in inv.assets)}. "
                    + (
                        f"Correlation shows '{target.source_alarm}' on {target.source_asset} as a leading "
                        f"indicator."
                        if target
                        else ""
                    ),
                )
        return out

    def _propose(self, t: Turn, asset_id: str, title: str, description: str) -> None:
        if t.mcp.has("draft_work_request"):
            t.proposed.append(
                ProposedAction(
                    type="work_request",
                    asset_id=asset_id,
                    title=title[:120],
                    description=description[:1900],
                    priority="P2",
                )
            )

    def _finalise_investigation(self, t: Turn) -> None:
        inv = t.inv
        rec = t.data.get("recommendation") or {}
        leading = (
            {
                (c.source_asset_id)
                for c in cause_candidates(t.data.get("correlation"), set(t.asset_ids))
                if c.upstream
            }
            if t.data.get("correlation")
            else set()
        )
        related = []
        for r in rec.get("related_assets_to_check") or []:
            refs = [t.refs.get("recommendations", "")]
            reason = r.get("reason", "")
            if r["asset_id"] in leading:
                reason += "; leading indicator in correlation analysis"
                refs.append(t.refs.get("correlation", ""))
            related.append(
                {
                    **r,
                    "reason": reason,
                    "refs": refs,
                    "score": (2 if r["asset_id"] in leading else 0) + min(r.get("alarms_within_2h", 0), 5),
                }
            )
        if not related:
            for a in inv.assets:
                for r in a.get("related_assets", []):
                    related.append(
                        {
                            **r,
                            "reason": "linked asset"
                            + (
                                "; leading indicator in correlation analysis"
                                if r["asset_id"] in leading
                                else ""
                            ),
                            "refs": [t.refs.get("metadata", "")],
                            "score": 2 if r["asset_id"] in leading else 0,
                        }
                    )
        related.sort(key=lambda r: -r["score"])
        if t.u.intent in {"related_assets", "investigate", "active_response"}:
            inv.related_assets = related

    # ================================================================== response
    async def _respond(self, t: Turn, t0: float) -> ChatResponse:
        u = t.u
        citations = t.cites.all()
        warnings = t.warnings + t.rag_notes
        status = "complete"
        if t.clarification:
            status = "needs_clarification"
        elif t.degraded or any(r.status in {"error", "unavailable"} for r in t.mcp.records):
            status = "partial"

        llm_ms = 0.0
        if t.clarification:
            markdown = f"**I need a bit more information.** {t.clarification}"
            answer = Answer(markdown=markdown, generated_by="deterministic", grounded=True)
        else:
            markdown = compose_markdown(
                u.intent,
                t.inv,
                citations,
                t.refs,
                t.warnings,
                " ".join(t.rag_notes) if t.rag_notes else None,
                notes=t.notes,
            )
            answer = Answer(
                markdown=markdown,
                generated_by="deterministic-grounded-composer",
                grounded=True,
                grounding_notes=[
                    f"{len(citations)} document citation(s)",
                    f"{sum(1 for r in t.mcp.records if r.status == 'success')} successful tool call(s)",
                ],
            )
            if self.llm is not None and (citations or t.mcp.records):
                allowed = {c.citation_id for c in citations} | {
                    r.ref for r in t.mcp.records if r.status == "success"
                }
                pack = evidence_pack(u.intent, t.inv, citations, t.cites.chunks, t.refs, warnings)
                text, notes, llm_ms = await llm_answer(
                    self.llm,
                    u.text,
                    pack,
                    allowed,
                    bool(citations),
                    any(r.status == "success" for r in t.mcp.records),
                )
                if text:
                    answer = Answer(
                        markdown=text,
                        generated_by=self.llm.name,
                        grounded=True,
                        grounding_notes=["LLM answer passed citation and safety validation"],
                    )
                else:
                    warnings.extend(notes)
                    answer.grounding_notes.extend(notes)

        return ChatResponse(
            conversation_id=t.trace.conversation_id or "",
            request_id=t.trace.request_id,
            trace_id=t.trace.trace_id,
            status=status,  # type: ignore[arg-type]
            intent=IntentInfo(
                name=u.intent, confidence=u.confidence, entities=u.entities(), rationale=u.rationale
            ),
            answer=answer,
            investigation=t.inv,
            citations=citations,
            tool_trace=t.mcp.records,
            retrieval=t.retrieval,
            discovered_tools=sorted(t.mcp.tools),
            warnings=warnings,
            notes=t.notes,
            suggested_follow_ups=self._follow_ups(t),
            proposed_actions=t.proposed,
            timings={
                "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                "llm_ms": llm_ms,
                "tools_ms": round(sum(r.duration_ms for r in t.mcp.records), 1),
                "retrieval_ms": round(sum(r.duration_ms for r in t.retrieval), 1),
            },
        )

    @staticmethod
    def _follow_ups(t: Turn) -> list[str]:
        f = t.inv.focal_alarm
        out = []
        if t.clarification:
            return [
                "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days",
                "Which alarm has the highest priority in EastRefinery, and why?",
            ]
        if f:
            if t.u.intent != "procedure_lookup":
                out.append("Which operating procedure applies to this alarm?")
            if t.u.intent != "consistency_check":
                out.append("Are the API recommendations consistent with the maintenance manual?")
            if t.u.intent != "related_assets":
                out.append("What related assets should be inspected for this alarm?")
        if t.inv.assets and t.u.intent != "investigate":
            out.append(
                f"Investigate recurring alarms on {t.inv.assets[0].get('asset_name', 'this asset')} "
                "over the last 90 days"
            )
        return out[:3]

    def _remember(self, t: Turn, response: ChatResponse) -> None:
        st = t.state
        if t.inv.assets:
            st.assets = [
                {k: a.get(k) for k in ("asset_id", "asset_name", "asset_type", "site", "unit")}
                for a in t.inv.assets[:3]
            ]
        if t.inv.focal_alarm:
            st.focal_alarm = {
                k: t.inv.focal_alarm.get(k)
                for k in (
                    "alarm_id",
                    "alarm_name",
                    "alarm_code",
                    "asset_id",
                    "asset_name",
                    "asset_type",
                    "severity",
                    "status",
                    "start_time",
                )
            }
        st.site = t.inv.scope.get("site") or st.site
        st.unit = t.inv.scope.get("unit") or st.unit
        st.lookback_days = t.lookback
        st.last_intent = t.u.intent
        st.turns.append({"role": "user", "text": t.u.text})
        st.turns.append(
            {"role": "assistant", "text": response.answer.markdown[:500], "status": response.status}
        )
        self.store.save(st)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _band_response(r: dict[str, Any]) -> str:
    return {"urgent": "< 5 min", "high": "< 15 min", "medium": "< 60 min"}.get(
        r.get("priority_band", ""), "next shift"
    )
