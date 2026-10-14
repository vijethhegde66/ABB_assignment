"""Typed MCP tool output contracts.

These Pydantic models become the ``outputSchema`` advertised by every tool and
are validated by the MCP SDK before results leave the server. ``extra="allow"``
is used on upstream-shaped records so additive API changes do not break
the contract, while the fields the copilot relies on are declared and typed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alarm_connector import CallRecord


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ToolTrace(BaseModel):
    """Execution metadata attached to every tool result (shown in the GUI trace panel)."""

    tool: str
    trace_id: str
    request_id: str
    duration_ms: float
    api_calls: list[CallRecord] = Field(default_factory=list)


# ---------------------------------------------------------------- assets
class AssetMatch(Open):
    asset_id: str
    asset_name: str
    asset_type: str
    site: str
    unit: str
    criticality: str
    match_score: float


class AssetSearchResult(BaseModel):
    query: str
    total: int
    results: list[AssetMatch]
    best_match: AssetMatch | None = Field(description="Highest scoring match, or null when nothing matched")
    ambiguous: bool = Field(description="True when several assets match with similar scores")
    trace: ToolTrace


class RelatedAsset(Open):
    asset_id: str
    asset_name: str
    asset_type: str
    relationship: str


class AssetMetadataResult(Open):
    asset_id: str
    asset_name: str
    asset_type: str
    site: str
    unit: str
    criticality: str
    description: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    operating_limits: dict[str, str] = Field(default_factory=dict)
    related_assets: list[RelatedAsset] = Field(default_factory=list)
    procedure_refs: list[str] = Field(default_factory=list)
    trace: ToolTrace


# ---------------------------------------------------------------- alarms
class AlarmRecord(Open):
    alarm_id: str
    asset_id: str
    asset_name: str
    alarm_name: str
    alarm_code: str
    alarm_type: str
    severity: str
    priority: str
    status: str
    acknowledged: bool
    start_time: str
    end_time: str | None = None
    ack_delay_seconds: float | None = None
    site: str
    unit: str


class Pagination(Open):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    pages_fetched: int = 1
    truncated: bool = False


class AlarmListResult(BaseModel):
    alarms: list[AlarmRecord]
    pagination: Pagination
    severity_counts: dict[str, int]
    trace: ToolTrace


class AlarmDetailResult(BaseModel):
    alarm: AlarmRecord
    trace: ToolTrace


class SummaryGroup(Open):
    group: dict[str, Any]
    alarm_count: int
    recurring_rate: float | None = None
    avg_ack_delay_seconds: float | None = None
    max_severity: str | None = None
    first_occurrence: str | None = None
    last_occurrence: str | None = None


class AlarmSummaryResult(BaseModel):
    time_range: dict[str, str]
    total_alarms: int
    kpis: dict[str, Any]
    groups: list[SummaryGroup]
    trace: ToolTrace


class TrendResult(BaseModel):
    bucket: str
    series: list[dict[str, Any]]
    trend: dict[str, Any]
    time_range: dict[str, str]
    trace: ToolTrace


class AlarmRef(Open):
    asset_id: str
    asset_name: str
    alarm_code: str
    alarm_name: str


class CorrelationPair(Open):
    source: AlarmRef
    target: AlarmRef
    support: int
    confidence: float
    lift: float
    avg_lag_minutes: float


class LeadingIndicator(Open):
    asset_id: str
    asset_name: str
    alarm_name: str
    support: int


class CorrelationResult(BaseModel):
    method: str
    scope_asset_ids: list[str]
    events_analyzed: int
    pairs: list[CorrelationPair]
    leading_indicators: list[LeadingIndicator]
    trace: ToolTrace


class FloodResult(BaseModel):
    total_alarms: int
    flood_count: int
    percent_alarms_in_flood: float
    flood_windows: list[dict[str, Any]]
    trace: ToolTrace


class RationalizationResult(BaseModel):
    candidate_count: int
    candidates: list[dict[str, Any]]
    trace: ToolTrace


class PriorityFactor(Open):
    factor: str
    points: float
    max_points: float


class PriorityScoreResult(Open):
    alarm_id: str
    alarm_name: str
    asset_id: str
    asset_name: str
    priority_score: float
    priority_band: str
    target_response_time: str
    factors: list[PriorityFactor]
    rationale: str
    trace: ToolTrace


class RankedAlarm(Open):
    rank: int
    alarm_id: str
    alarm_name: str
    asset_name: str
    priority_score: float
    priority_band: str
    rationale: str


class PriorityRankingResult(BaseModel):
    ranked: list[RankedAlarm]
    failed: list[dict[str, Any]] = Field(description="Alarms that could not be scored (partial failure)")
    trace: ToolTrace


class Recommendation(Open):
    step: int
    action: str
    rationale: str
    urgency: str
    reference_procedure: str | None = None
    source: str


class OperatorActionsResult(Open):
    alarm_id: str
    alarm_name: str
    alarm_code: str
    asset_id: str
    asset_name: str
    recommendations: list[Recommendation]
    confidence: float
    related_assets_to_check: list[dict[str, Any]] | None = None
    historical_pattern: dict[str, Any] | None = None
    asset_context: dict[str, Any] | None = None
    trace: ToolTrace


class KpiResult(BaseModel):
    calculation_id: str
    calculation_type: str
    result: dict[str, Any]
    benchmark: str | None = None
    filters: dict[str, Any]
    trace: ToolTrace


class KpiDefinitionsResult(BaseModel):
    kpis: list[dict[str, Any]]
    trace: ToolTrace
