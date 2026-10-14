"""Domain and API models of the copilot (the contract consumed by the GUI)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from rag.retrieval.retriever import Citation

ToolStatus = Literal["success", "error", "invalid_arguments", "unavailable", "skipped", "denied"]
ResponseStatus = Literal["complete", "partial", "needs_clarification", "failed"]
Consistency = Literal["supported", "conflict", "not_covered"]


class ToolCallRecord(BaseModel):
    """One MCP tool invocation as seen by the copilot (rendered in the trace panel)."""

    ref: str = Field(description="Evidence reference used in answers, e.g. T3")
    step: str = Field(description="Orchestration stage that issued the call")
    server: str
    tool: str
    arguments: dict[str, Any]
    status: ToolStatus
    duration_ms: float = 0.0
    error: dict[str, Any] | None = None
    api_calls: list[dict[str, Any]] = Field(default_factory=list)
    retries: int = 0
    result: dict[str, Any] | None = Field(default=None, description="Raw structured tool result")
    started_at: str | None = None
    trace_id: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class IntentInfo(BaseModel):
    name: str
    confidence: float
    entities: dict[str, Any]
    rationale: str


class LikelyCause(BaseModel):
    cause: str
    evidence: list[str]
    confidence: Literal["high", "medium", "low"]
    refs: list[str]


class ActionItem(BaseModel):
    action: str
    source: Literal["procedure", "api", "investigation"]
    urgency: str | None = None
    consistency: Consistency | None = None
    note: str | None = None
    refs: list[str] = Field(default_factory=list)


class Investigation(BaseModel):
    scope: dict[str, Any] = Field(default_factory=dict)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    time_window: dict[str, str] | None = None
    alarm_summary: dict[str, Any] | None = None
    trend: dict[str, Any] | None = None
    active_alarms: list[dict[str, Any]] = Field(default_factory=list)
    focal_alarm: dict[str, Any] | None = None
    priority: dict[str, Any] | None = None
    ranked_alarms: list[dict[str, Any]] = Field(default_factory=list)
    correlations: list[dict[str, Any]] = Field(default_factory=list)
    likely_causes: list[LikelyCause] = Field(default_factory=list)
    related_assets: list[dict[str, Any]] = Field(default_factory=list)
    api_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list, description="Citation ids of applicable procedures")
    work_orders: list[dict[str, Any]] = Field(default_factory=list)
    kpis: list[dict[str, Any]] = Field(default_factory=list)


class RetrievalTrace(BaseModel):
    purpose: str
    query: str
    filters: dict[str, Any]
    confidence: str
    confidence_reason: str
    hits: list[dict[str, Any]]
    quarantined: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0.0


class Answer(BaseModel):
    markdown: str
    generated_by: str
    grounded: bool
    grounding_notes: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    type: Literal["work_request"]
    asset_id: str
    title: str
    description: str
    priority: Literal["P1", "P2", "P3", "P4"] = "P3"
    requires_confirmation: bool = True


class ChatResponse(BaseModel):
    conversation_id: str
    request_id: str
    trace_id: str
    status: ResponseStatus
    intent: IntentInfo
    answer: Answer
    investigation: Investigation
    citations: list[Citation]
    tool_trace: list[ToolCallRecord]
    retrieval: list[RetrievalTrace]
    discovered_tools: list[str]
    warnings: list[str]
    notes: list[str] = Field(default_factory=list, description="Informational context notes (not problems)")
    suggested_follow_ups: list[str]
    proposed_actions: list[ProposedAction]
    timings: dict[str, float]
