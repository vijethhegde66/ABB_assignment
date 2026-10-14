"""Alarm Management MCP server: exposes Alarm Management API capabilities as typed MCP tools."""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from alarm_connector import AlarmApiClient, AlarmApiError, CallRecord
from alarm_mcp.schemas import (
    AlarmDetailResult,
    AlarmListResult,
    AlarmRecord,
    AlarmSummaryResult,
    AssetMetadataResult,
    AssetSearchResult,
    CorrelationResult,
    FloodResult,
    KpiDefinitionsResult,
    KpiResult,
    OperatorActionsResult,
    Pagination,
    PriorityRankingResult,
    PriorityScoreResult,
    RankedAlarm,
    RationalizationResult,
    ToolTrace,
    TrendResult,
)
from platform_common import TraceContext, get_logger, new_id, trace_scope

log = get_logger("alarm_mcp")

SERVER_NAME = "alarm-management"
INSTRUCTIONS = (
    "Read-only tools over the plant Alarm Management system. Typical chain: search_assets -> "
    "get_asset_metadata -> get_alarms / summarize_alarms -> correlate_alarms -> score_alarm_priority -> "
    "recommend_operator_actions. All timestamps are ISO-8601 UTC. Errors are returned as JSON with a stable "
    "error_code (INVALID_ARGUMENT, NOT_FOUND, AUTHENTICATION_FAILED, RATE_LIMITED, UPSTREAM_UNAVAILABLE, "
    "UPSTREAM_TIMEOUT, BAD_UPSTREAM_RESPONSE)."
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True, idempotent_hint=True, destructive_hint=False, open_world_hint=False
)

# ----------------------------------------------------------------- reusable parameter types
AssetIdParam = Annotated[
    str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$", description="Asset identifier, e.g. NP-BFP-101")
]
AlarmIdParam = Annotated[
    str, Field(pattern=r"^ALM-\d{4,10}$", description="Alarm identifier, e.g. ALM-0001234")
]
SeverityParam = Literal["low", "medium", "high", "critical"]
StartParam = Annotated[
    datetime | None, Field(description="Window start (ISO-8601). Defaults to end - lookback_days")
]
EndParam = Annotated[datetime | None, Field(description="Window end (ISO-8601). Defaults to now")]
LookbackParam = Annotated[int, Field(ge=1, le=400, description="Used when start_time is omitted")]
SiteParam = Annotated[str | None, Field(max_length=64, description="Site name, e.g. EastRefinery")]
UnitParam = Annotated[str | None, Field(max_length=64, description="Unit name, e.g. Unit 2")]


def _invalid(message: str) -> ToolError:
    return ToolError(json.dumps({"error_code": "INVALID_ARGUMENT", "message": message, "retryable": False}))


def resolve_window(start: datetime | None, end: datetime | None, lookback_days: int) -> dict[str, str]:
    end_dt = end or datetime.now(UTC).replace(microsecond=0)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)
    start_dt = start or end_dt - timedelta(days=lookback_days)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)
    if start_dt >= end_dt:
        raise _invalid("start_time must be before end_time")
    if (end_dt - start_dt).days > 400:
        raise _invalid("time window must not exceed 400 days")
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "start_time": start_dt.astimezone(UTC).strftime(fmt),
        "end_time": end_dt.astimezone(UTC).strftime(fmt),
    }


class _ToolRun:
    """Captures trace context, timing and API call records for one tool invocation."""

    def __init__(self, tool: str, ctx: Context | None) -> None:
        self.tool = tool
        self.calls: list[CallRecord] = []
        meta: dict[str, Any] = {}
        headers: Any = None
        if ctx is not None:
            try:
                meta = dict(ctx.request_context.meta or {})
            except (AttributeError, ValueError):
                meta = {}
            try:
                headers = ctx.headers
            except (AttributeError, ValueError):
                headers = None
        trace_id = str(meta.get("trace_id") or (headers or {}).get("x-trace-id") or new_id("trace"))
        self.trace = TraceContext(
            trace_id=trace_id,
            request_id=str(meta.get("request_id") or new_id("req")),
            conversation_id=meta.get("conversation_id"),
        )
        self.started = time.perf_counter()

    def record(self, call: CallRecord) -> None:
        self.calls.append(call)

    def tool_trace(self) -> ToolTrace:
        return ToolTrace(
            tool=self.tool,
            trace_id=self.trace.trace_id,
            request_id=self.trace.request_id,
            duration_ms=round((time.perf_counter() - self.started) * 1000, 1),
            api_calls=self.calls,
        )

    def fail(self, exc: AlarmApiError) -> ToolError:
        call = getattr(exc, "call_record", None)
        if call is not None:
            self.calls.append(call)
        payload = exc.to_dict()
        payload["tool"] = self.tool
        payload["trace_id"] = self.trace.trace_id
        payload["api_calls"] = [c.model_dump() for c in self.calls]
        log.warning(
            "tool_failed", extra={"tool": self.tool, "error_code": exc.code, "attempts": exc.attempts}
        )
        return ToolError(json.dumps(payload, default=str))


def build_server(api: AlarmApiClient, *, close_client_on_shutdown: bool = False) -> MCPServer:
    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if close_client_on_shutdown:
                await api.aclose()

    mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version="1.0.0", lifespan=lifespan)

    async def run(tool: str, ctx: Context | None, fn: Any) -> Any:
        r = _ToolRun(tool, ctx)
        with trace_scope(r.trace):
            log.info("tool_invoked", extra={"tool": tool})
            try:
                result = await fn(r)
            except AlarmApiError as exc:
                raise r.fail(exc) from exc
            log.info("tool_completed", extra={"tool": tool, "api_calls": len(r.calls)})
            return result

    # ------------------------------------------------------------------ asset search
    @mcp.tool(
        title="Search assets",
        description=(
            "Resolve a free-text asset name (e.g. 'Boiler Feed Pump 101', 'compressor', 'K-201') to asset "
            "identifiers. Returns ranked matches with a best_match and an ambiguity flag. Use this before any "
            "asset-scoped alarm tool."
        ),
        annotations=READ_ONLY,
    )
    async def search_assets(
        query: Annotated[str, Field(min_length=1, max_length=120, description="Asset name or keyword")],
        site: SiteParam = None,
        unit: UnitParam = None,
        asset_type: Annotated[
            str | None, Field(max_length=64, description="e.g. boiler_feed_pump, motor")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
        ctx: Context | None = None,
    ) -> AssetSearchResult:
        async def body(r: _ToolRun) -> AssetSearchResult:
            resp = await api.search_assets(
                query.strip(), site=site, unit=unit, asset_type=asset_type, limit=limit
            )
            r.record(resp.call)
            results = resp.data.get("results", [])
            best = results[0] if results else None
            ambiguous = bool(
                len(results) > 1
                and best
                and best["match_score"] < 1.0
                and results[1]["match_score"] >= best["match_score"] - 0.05
            )
            return AssetSearchResult(
                query=query,
                total=len(results),
                results=results,
                best_match=best,
                ambiguous=ambiguous,
                trace=r.tool_trace(),
            )

        return await run("search_assets", ctx, body)

    @mcp.tool(
        title="Get asset metadata",
        description=(
            "Fetch asset master data: type, criticality, operating limits, related assets (driver, suction source, "
            "lube system, standby unit...) and referenced procedure document ids."
        ),
        annotations=READ_ONLY,
    )
    async def get_asset_metadata(asset_id: AssetIdParam, ctx: Context | None = None) -> AssetMetadataResult:
        async def body(r: _ToolRun) -> AssetMetadataResult:
            resp = await api.get_asset_metadata(asset_id)
            r.record(resp.call)
            return AssetMetadataResult(**resp.data, trace=r.tool_trace())

        return await run("get_asset_metadata", ctx, body)

    # ------------------------------------------------------------------ alarm retrieval
    @mcp.tool(
        title="Get alarms",
        description=(
            "Retrieve alarm events filtered by asset, site, unit, status (active|cleared), severity and time window. "
            "Supports pagination; set fetch_all_pages=true to follow pages up to max_pages."
        ),
        annotations=READ_ONLY,
    )
    async def get_alarms(
        asset_id: AssetIdParam | None = None,
        site: SiteParam = None,
        unit: UnitParam = None,
        status: Literal["active", "cleared"] | None = None,
        severity: Annotated[list[SeverityParam] | None, Field(max_length=4)] = None,
        alarm_name: Annotated[str | None, Field(max_length=120)] = None,
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        page: Annotated[int, Field(ge=1, le=10000)] = 1,
        page_size: Annotated[int, Field(ge=1, le=500)] = 100,
        sort_by: Literal["start_time", "severity", "priority", "asset_name", "alarm_name"] = "start_time",
        sort_order: Literal["asc", "desc"] = "desc",
        fetch_all_pages: bool = False,
        max_pages: Annotated[int, Field(ge=1, le=20)] = 5,
        ctx: Context | None = None,
    ) -> AlarmListResult:
        if not (asset_id or site or unit):
            raise _invalid("provide at least one of asset_id, site or unit")
        window = (
            resolve_window(start_time, end_time, lookback_days)
            if status != "active" or start_time or end_time
            else {}
        )

        async def body(r: _ToolRun) -> AlarmListResult:
            params: dict[str, Any] = {
                "asset_id": asset_id,
                "site": site,
                "unit": unit,
                "status": status,
                "severity": severity,
                "alarm_name": alarm_name,
                "page_size": page_size,
                "sort_by": sort_by,
                "sort_order": sort_order,
                **window,
            }
            if fetch_all_pages:
                rows, pagination, calls = await api.list_alarms_all_pages(
                    max_pages=max_pages, page=page, **params
                )
                for c in calls:
                    r.record(c)
            else:
                resp = await api.list_alarms(page=page, **params)
                r.record(resp.call)
                rows = resp.data.get("data", [])
                pagination = resp.data.get("pagination", {})
            counts = Counter(a["severity"] for a in rows)
            return AlarmListResult(
                alarms=[AlarmRecord.model_validate(a) for a in rows],
                pagination=Pagination.model_validate(pagination),
                severity_counts=dict(counts),
                trace=r.tool_trace(),
            )

        return await run("get_alarms", ctx, body)

    @mcp.tool(
        title="Get alarm details", description="Fetch one alarm event by alarm_id.", annotations=READ_ONLY
    )
    async def get_alarm_details(alarm_id: AlarmIdParam, ctx: Context | None = None) -> AlarmDetailResult:
        async def body(r: _ToolRun) -> AlarmDetailResult:
            resp = await api.get_alarm(alarm_id)
            r.record(resp.call)
            return AlarmDetailResult(alarm=AlarmRecord.model_validate(resp.data), trace=r.tool_trace())

        return await run("get_alarm_details", ctx, body)

    # ------------------------------------------------------------------ summary / trends
    @mcp.tool(
        title="Summarize alarms",
        description=(
            "Aggregate alarms over a window and compute KPIs (alarm_count, recurring_rate, avg_ack_delay, "
            "critical_count, chattering_count, standing_count, suppression_candidate_rate) grouped by fields such as "
            "alarm_name, asset_name or severity. Use it to find recurring alarms."
        ),
        annotations=READ_ONLY,
    )
    async def summarize_alarms(
        asset_ids: Annotated[list[AssetIdParam] | None, Field(max_length=20)] = None,
        site: SiteParam = None,
        unit: UnitParam = None,
        severity: Annotated[list[SeverityParam] | None, Field(max_length=4)] = None,
        group_by: Annotated[
            list[
                Literal[
                    "alarm_name",
                    "alarm_code",
                    "asset_id",
                    "asset_name",
                    "severity",
                    "alarm_type",
                    "unit",
                    "site",
                ]
            ],
            Field(min_length=1, max_length=4),
        ] = ["alarm_name"],  # noqa: B006 - immutable usage, required for JSON schema default
        kpis: Annotated[
            list[
                Literal[
                    "alarm_count",
                    "recurring_rate",
                    "avg_ack_delay",
                    "critical_count",
                    "chattering_count",
                    "standing_count",
                    "suppression_candidate_rate",
                    "unacknowledged_count",
                ]
            ],
            Field(min_length=1),
        ] = ["alarm_count", "recurring_rate", "avg_ack_delay"],  # noqa: B006
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        ctx: Context | None = None,
    ) -> AlarmSummaryResult:
        if not (asset_ids or site or unit):
            raise _invalid("provide at least one of asset_ids, site or unit")
        window = resolve_window(start_time, end_time, lookback_days)

        async def body(r: _ToolRun) -> AlarmSummaryResult:
            payload = {
                "asset_ids": asset_ids,
                "site": site,
                "unit": unit,
                "time_range": window,
                "severity": severity,
                "group_by": group_by,
                "kpis": kpis,
            }
            resp = await api.alarm_summary({k: v for k, v in payload.items() if v is not None})
            r.record(resp.call)
            d = resp.data
            return AlarmSummaryResult(
                time_range=d["time_range"],
                total_alarms=d["total_alarms"],
                kpis=d["kpis"],
                groups=d["groups"][:50],
                trace=r.tool_trace(),
            )

        return await run("summarize_alarms", ctx, body)

    @mcp.tool(
        title="Get alarm trends",
        description="Time-bucketed alarm counts / acknowledgement delay with a trend direction (increasing|stable|decreasing).",
        annotations=READ_ONLY,
    )
    async def get_alarm_trends(
        asset_ids: Annotated[list[AssetIdParam] | None, Field(max_length=20)] = None,
        site: SiteParam = None,
        unit: UnitParam = None,
        severity: Annotated[list[SeverityParam] | None, Field(max_length=4)] = None,
        bucket: Literal["hourly", "daily", "weekly"] = "weekly",
        metrics: list[Literal["alarm_count", "avg_ack_delay", "critical_count"]] = ["alarm_count"],  # noqa: B006
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        ctx: Context | None = None,
    ) -> TrendResult:
        if not (asset_ids or site or unit):
            raise _invalid("provide at least one of asset_ids, site or unit")
        window = resolve_window(start_time, end_time, lookback_days)

        async def body(r: _ToolRun) -> TrendResult:
            payload = {
                "asset_ids": asset_ids,
                "site": site,
                "unit": unit,
                "severity": severity,
                "time_range": window,
                "bucket": bucket,
                "metrics": metrics,
            }
            resp = await api.alarm_trends({k: v for k, v in payload.items() if v is not None})
            r.record(resp.call)
            d = resp.data
            return TrendResult(
                bucket=d["bucket"],
                series=d["series"],
                trend=d["trend"],
                time_range=d["time_range"],
                trace=r.tool_trace(),
            )

        return await run("get_alarm_trends", ctx, body)

    # ------------------------------------------------------------------ advanced analytics
    @mcp.tool(
        title="Correlate alarms",
        description=(
            "Find alarms that co-occur / precede each other within a lag window across the given assets and (by "
            "default) their related assets. Returns pairs with support, confidence, lift and leading indicators - "
            "use it to identify likely contributing factors."
        ),
        annotations=READ_ONLY,
    )
    async def correlate_alarms(
        asset_ids: Annotated[list[AssetIdParam], Field(min_length=1, max_length=10)],
        include_related: bool = True,
        lag_window_minutes: Annotated[int, Field(ge=1, le=240)] = 15,
        severity_threshold: SeverityParam = "medium",
        min_support: Annotated[int, Field(ge=1, le=1000)] = 2,
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        ctx: Context | None = None,
    ) -> CorrelationResult:
        window = resolve_window(start_time, end_time, lookback_days)

        async def body(r: _ToolRun) -> CorrelationResult:
            resp = await api.alarm_correlation(
                {
                    "asset_ids": asset_ids,
                    "time_range": window,
                    "correlation_method": "cooccurrence",
                    "lag_window_minutes": lag_window_minutes,
                    "severity_threshold": severity_threshold,
                    "min_support": min_support,
                    "include_related": include_related,
                }
            )
            r.record(resp.call)
            d = resp.data
            return CorrelationResult(
                method=d["method"],
                scope_asset_ids=d["scope_asset_ids"],
                events_analyzed=d["events_analyzed"],
                pairs=d["pairs"],
                leading_indicators=d["leading_indicators"],
                trace=r.tool_trace(),
            )

        return await run("correlate_alarms", ctx, body)

    @mcp.tool(
        title="Analyze alarm floods",
        description="Detect alarm flood periods (>= threshold_count alarms within rolling_window_minutes) for a unit/site.",
        annotations=READ_ONLY,
    )
    async def analyze_alarm_floods(
        site: SiteParam = None,
        unit: UnitParam = None,
        threshold_count: Annotated[int, Field(ge=2, le=1000)] = 10,
        rolling_window_minutes: Annotated[int, Field(ge=1, le=1440)] = 10,
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        ctx: Context | None = None,
    ) -> FloodResult:
        if not (site or unit):
            raise _invalid("provide site or unit")
        window = resolve_window(start_time, end_time, lookback_days)

        async def body(r: _ToolRun) -> FloodResult:
            resp = await api.flood_analysis(
                {
                    k: v
                    for k, v in {
                        "site": site,
                        "unit": unit,
                        "time_range": window,
                        "threshold_count": threshold_count,
                        "rolling_window_minutes": rolling_window_minutes,
                    }.items()
                    if v is not None
                }
            )
            r.record(resp.call)
            d = resp.data
            return FloodResult(
                total_alarms=d["total_alarms"],
                flood_count=d["flood_count"],
                percent_alarms_in_flood=d["percent_alarms_in_flood"],
                flood_windows=d["flood_windows"][:20],
                trace=r.tool_trace(),
            )

        return await run("analyze_alarm_floods", ctx, body)

    @mcp.tool(
        title="Find rationalization candidates",
        description="Identify chattering, stale, frequent or often-unacknowledged alarms that are candidates for rationalization.",
        annotations=READ_ONLY,
    )
    async def find_rationalization_candidates(
        asset_ids: Annotated[list[AssetIdParam] | None, Field(max_length=20)] = None,
        site: SiteParam = None,
        unit: UnitParam = None,
        recurrence_threshold: Annotated[int, Field(ge=1, le=10000)] = 5,
        stale_minutes_threshold: Annotated[int, Field(ge=1, le=100000)] = 180,
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        ctx: Context | None = None,
    ) -> RationalizationResult:
        if not (asset_ids or site or unit):
            raise _invalid("provide at least one of asset_ids, site or unit")
        window = resolve_window(start_time, end_time, lookback_days)

        async def body(r: _ToolRun) -> RationalizationResult:
            resp = await api.rationalization_candidates(
                {
                    k: v
                    for k, v in {
                        "asset_ids": asset_ids,
                        "site": site,
                        "unit": unit,
                        "time_range": window,
                        "recurrence_threshold": recurrence_threshold,
                        "stale_minutes_threshold": stale_minutes_threshold,
                    }.items()
                    if v is not None
                }
            )
            r.record(resp.call)
            return RationalizationResult(
                candidate_count=resp.data["candidate_count"],
                candidates=resp.data["candidates"][:30],
                trace=r.tool_trace(),
            )

        return await run("find_rationalization_candidates", ctx, body)

    @mcp.tool(
        title="Score alarm priority",
        description="Compute a 0-100 priority score for one alarm with the contributing factors and target response time.",
        annotations=READ_ONLY,
    )
    async def score_alarm_priority(alarm_id: AlarmIdParam, ctx: Context | None = None) -> PriorityScoreResult:
        async def body(r: _ToolRun) -> PriorityScoreResult:
            resp = await api.priority_score(alarm_id)
            r.record(resp.call)
            return PriorityScoreResult(**resp.data, trace=r.tool_trace())

        return await run("score_alarm_priority", ctx, body)

    @mcp.tool(
        title="Rank alarm priorities",
        description=(
            "Score several alarms (max 20) concurrently and return them ranked by priority score. Alarms that fail "
            "to score are reported in 'failed' instead of failing the whole call (partial results)."
        ),
        annotations=READ_ONLY,
    )
    async def rank_alarm_priorities(
        alarm_ids: Annotated[list[AlarmIdParam], Field(min_length=1, max_length=20)],
        ctx: Context | None = None,
    ) -> PriorityRankingResult:
        async def body(r: _ToolRun) -> PriorityRankingResult:
            async def one(aid: str) -> tuple[str, Any]:
                try:
                    resp = await api.priority_score(aid)
                    r.record(resp.call)
                    return aid, resp.data
                except AlarmApiError as exc:
                    call = getattr(exc, "call_record", None)
                    if call is not None:
                        r.record(call)
                    return aid, exc

            results = await asyncio.gather(*(one(a) for a in dict.fromkeys(alarm_ids)))
            ok = [d for _, d in results if isinstance(d, dict)]
            failed = [{"alarm_id": a, **e.to_dict()} for a, e in results if isinstance(e, AlarmApiError)]
            if not ok and failed:
                first = next(e for _, e in results if isinstance(e, AlarmApiError))
                raise first
            ok.sort(key=lambda d: -d["priority_score"])
            ranked = [
                {
                    "rank": i + 1,
                    **{
                        k: d[k]
                        for k in (
                            "alarm_id",
                            "alarm_name",
                            "asset_name",
                            "priority_score",
                            "priority_band",
                            "rationale",
                        )
                    },
                }
                for i, d in enumerate(ok)
            ]
            return PriorityRankingResult(
                ranked=[RankedAlarm.model_validate(x) for x in ranked], failed=failed, trace=r.tool_trace()
            )

        return await run("rank_alarm_priorities", ctx, body)

    @mcp.tool(
        title="Recommend operator actions",
        description=(
            "Get the Alarm Management system's recommended operator actions for one alarm, optionally with related "
            "assets to inspect, asset context and historical pattern. Recommendations come from the vendor rule "
            "engine and should be cross-checked against controlled site procedures."
        ),
        annotations=READ_ONLY,
    )
    async def recommend_operator_actions(
        alarm_id: AlarmIdParam,
        include_related: bool = True,
        include_asset_context: bool = True,
        include_historical_pattern: bool = True,
        ctx: Context | None = None,
    ) -> OperatorActionsResult:
        async def body(r: _ToolRun) -> OperatorActionsResult:
            resp = await api.operator_actions(
                {
                    "alarm_id": alarm_id,
                    "include_related": include_related,
                    "include_asset_context": include_asset_context,
                    "include_historical_pattern": include_historical_pattern,
                }
            )
            r.record(resp.call)
            return OperatorActionsResult(**resp.data, trace=r.tool_trace())

        return await run("recommend_operator_actions", ctx, body)

    # ------------------------------------------------------------------ KPIs
    @mcp.tool(
        title="Calculate KPI",
        description=(
            "Calculate an alarm-management KPI (alarm_flood_index, critical_alarm_density, "
            "operator_response_efficiency, nuisance_alarm_score, alarm_rate_per_hour) for a site/unit/assets. "
            "Chains the API's generate + execute calculation endpoints."
        ),
        annotations=READ_ONLY,
    )
    async def calculate_kpi(
        calculation_type: Literal[
            "alarm_flood_index",
            "critical_alarm_density",
            "operator_response_efficiency",
            "nuisance_alarm_score",
            "alarm_rate_per_hour",
        ],
        site: SiteParam = None,
        unit: UnitParam = None,
        asset_ids: Annotated[list[AssetIdParam] | None, Field(max_length=20)] = None,
        start_time: StartParam = None,
        end_time: EndParam = None,
        lookback_days: LookbackParam = 90,
        ctx: Context | None = None,
    ) -> KpiResult:
        window = resolve_window(start_time, end_time, lookback_days)
        filters = {
            k: v for k, v in {"site": site, "unit": unit, "asset_ids": asset_ids, **window}.items() if v
        }

        async def body(r: _ToolRun) -> KpiResult:
            gen = await api.generate_calculation({"calculation_type": calculation_type, "filters": filters})
            r.record(gen.call)
            calc_id = gen.data["calculation_id"]
            exe = await api.execute_calculation({"calculation_id": calc_id, "filters": filters})
            r.record(exe.call)
            d = exe.data
            return KpiResult(
                calculation_id=calc_id,
                calculation_type=d["calculation_type"],
                result=d["result"],
                benchmark=d.get("benchmark"),
                filters=d.get("filters", filters),
                trace=r.tool_trace(),
            )

        return await run("calculate_kpi", ctx, body)

    @mcp.tool(
        title="List KPI definitions",
        description="List supported alarm KPI definitions and benchmarks.",
        annotations=READ_ONLY,
    )
    async def list_kpi_definitions(ctx: Context | None = None) -> KpiDefinitionsResult:
        async def body(r: _ToolRun) -> KpiDefinitionsResult:
            resp = await api.kpi_definitions()
            r.record(resp.call)
            return KpiDefinitionsResult(kpis=resp.data["kpis"], trace=r.tool_trace())

        return await run("list_kpi_definitions", ctx, body)

    # ------------------------------------------------------------------ health (HTTP transport only)
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        try:
            await api.health()
            upstream = "ok"
        except AlarmApiError as exc:
            upstream = exc.code
        tools = await mcp.list_tools()
        return JSONResponse(
            {"status": "ok", "server": SERVER_NAME, "tools": len(tools), "alarm_api": upstream}
        )

    return mcp
