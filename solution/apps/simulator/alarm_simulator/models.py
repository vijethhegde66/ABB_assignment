"""Request contracts (derived from the Postman collections). Unknown fields are rejected."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["low", "medium", "high", "critical"]
AlarmType = Literal["process", "safety", "equipment", "device", "electrical"]
AssetId = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")]
AlarmId = Annotated[str, Field(pattern=r"^ALM-\d{4,10}$")]
GroupBy = Literal[
    "alarm_name", "alarm_code", "asset_id", "asset_name", "severity", "alarm_type", "unit", "site", "priority"
]
Kpi = Literal[
    "alarm_count",
    "recurring_rate",
    "avg_ack_delay",
    "critical_count",
    "suppression_candidate_rate",
    "chattering_count",
    "standing_count",
    "unacknowledged_count",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(Strict):
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def _order(self) -> TimeRange:
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("start_time and end_time must include a timezone (e.g. 2026-05-01T00:00:00Z)")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if (self.end_time - self.start_time).days > 400:
            raise ValueError("time range must not exceed 400 days")
        return self


class ScopedRequest(Strict):
    asset_ids: list[AssetId] | None = Field(default=None, max_length=50)
    site: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    time_range: TimeRange


class SummaryRequest(ScopedRequest):
    severity: list[Severity] | None = None
    alarm_types: list[AlarmType] | None = None
    group_by: list[GroupBy] = Field(default_factory=lambda: ["alarm_name"], max_length=4)  # type: ignore[arg-type]
    kpis: list[Kpi] = Field(default_factory=lambda: ["alarm_count"])  # type: ignore[arg-type]


class TrendsRequest(ScopedRequest):
    severity: list[Severity] | None = None
    bucket: Literal["hourly", "daily", "weekly"] = "daily"
    metrics: list[Literal["alarm_count", "avg_ack_delay", "critical_count"]] = Field(
        default_factory=lambda: ["alarm_count"]  # type: ignore[arg-type]
    )


class CorrelationRequest(Strict):
    asset_ids: list[AssetId] = Field(min_length=1, max_length=20)
    time_range: TimeRange
    correlation_method: Literal["cooccurrence", "sequence"] = "cooccurrence"
    lag_window_minutes: int = Field(default=15, ge=1, le=240)
    severity_threshold: Severity = "medium"
    min_support: int = Field(default=1, ge=1, le=1000)
    include_related: bool = True


class FloodRequest(Strict):
    site: str | None = None
    unit: str | None = None
    asset_ids: list[AssetId] | None = None
    time_range: TimeRange
    threshold_count: int = Field(default=10, ge=2, le=1000)
    rolling_window_minutes: int = Field(default=10, ge=1, le=1440)


class RationalizationRequest(ScopedRequest):
    recurrence_threshold: int = Field(default=5, ge=1, le=10000)
    stale_minutes_threshold: int = Field(default=180, ge=1, le=100000)


class PriorityRequest(Strict):
    alarm_id: AlarmId


class RecommendationRequest(Strict):
    alarm_id: AlarmId
    include_related: bool = False
    include_asset_context: bool = False
    include_historical_pattern: bool = False


class CalcFilters(Strict):
    site: str | None = None
    unit: str | None = None
    asset_ids: list[AssetId] | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class CalcGenerateRequest(Strict):
    calculation_type: str = Field(min_length=3, max_length=64, pattern=r"^[a-z_]+$")
    filters: CalcFilters = Field(default_factory=CalcFilters)


class CalcExecuteRequest(Strict):
    calculation_id: str = Field(pattern=r"^CALC-[a-f0-9]{12}$")
    filters: CalcFilters | None = None


class FaultRule(Strict):
    path_prefix: str = Field(min_length=1, max_length=128, pattern=r"^/")
    mode: Literal["error", "timeout", "latency"]
    status_code: int = Field(default=503, ge=400, le=599)
    delay_ms: int = Field(default=0, ge=0, le=120000)
    remaining: int | None = Field(
        default=None, ge=1, description="Number of requests to affect; null = until cleared"
    )
