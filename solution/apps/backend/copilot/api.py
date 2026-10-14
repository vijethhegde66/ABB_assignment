"""Copilot HTTP API (consumed by the GUI)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from copilot import __version__
from copilot.config import CopilotSettings
from copilot.llm import build_provider
from copilot.mcp_client import McpServerConfig, McpToolRegistry
from copilot.memory import ConversationStore
from copilot.models import ChatRequest, ChatResponse
from copilot.orchestrator import Orchestrator
from copilot.rag_service import RagService, RagUnavailableError
from platform_common import TraceContext, configure_logging, get_logger, new_id, trace_scope
from rag.retrieval.retriever import RetrievalFilters, build_citations

log = get_logger("copilot.api")


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    filters: RetrievalFilters | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class WorkRequestDraft(BaseModel):
    asset_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=5, max_length=2000)
    priority: Literal["P1", "P2", "P3", "P4"] = "P3"


class WorkRequestConfirm(BaseModel):
    draft_id: str = Field(pattern=r"^DRAFT-[a-f0-9]{10}$")
    approval_token: str = Field(min_length=10, max_length=200)
    approved_by: str = Field(min_length=2, max_length=80)
    confirm: bool = Field(description="Must be true: explicit human confirmation of the write operation")


class FaultRequest(BaseModel):
    path_prefix: str = Field(pattern=r"^/[a-z0-9/_-]{1,100}$")
    mode: Literal["error", "timeout", "latency"] = "error"
    status_code: int = Field(default=503, ge=400, le=599)
    delay_ms: int = Field(default=0, ge=0, le=60000)
    remaining: int | None = Field(default=None, ge=1, le=100)


def build_registry(s: CopilotSettings) -> McpToolRegistry:
    servers = [
        McpServerConfig(
            "alarm-management",
            url=s.mcp_server_url,
            auth_token=s.mcp_auth_token.get_secret_value(),
            required=True,
        )
    ]
    if s.work_orders_mcp_url:
        servers.append(
            McpServerConfig(
                "work-orders",
                url=s.work_orders_mcp_url,
                auth_token=s.work_orders_mcp_token.get_secret_value(),
                required=False,
            )
        )
    return McpToolRegistry(
        servers, call_timeout_s=s.mcp_tool_timeout_seconds, connect_timeout_s=s.mcp_connect_timeout_seconds
    )


def create_app(
    settings: CopilotSettings | None = None,
    *,
    registry: McpToolRegistry | None = None,
    rag: RagService | None = None,
) -> FastAPI:
    s = settings or CopilotSettings()
    configure_logging("copilot-backend", s.log_level)
    registry = registry or build_registry(s)
    rag = rag or RagService(s.rag_index_path, s.document_path, s.embedding_provider, s.rag_auto_ingest)
    llm = build_provider(
        s.llm_provider, s.llm_api_key.get_secret_value(), s.llm_model, s.llm_timeout_seconds, s.llm_effort
    )
    store = ConversationStore()
    orchestrator = Orchestrator(
        registry,
        rag,
        store,
        llm,
        default_lookback_days=s.default_lookback_days,
        reference_time=s.reference_time,
    )

    app = FastAPI(title="Alarm Investigation Copilot API", version=__version__)
    app.state.orchestrator = orchestrator
    app.state.registry = registry
    app.state.rag = rag
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_list(),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Request-Id"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        t0 = time.perf_counter()
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'"
        )
        response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        return response

    # ------------------------------------------------------------------ chat
    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        return await orchestrator.chat(req.message.strip(), req.conversation_id)

    @app.get("/api/conversations/{conversation_id}")
    async def conversation(conversation_id: str) -> dict[str, Any]:
        st = store.peek(conversation_id)
        if st is None:
            raise HTTPException(404, "conversation not found")
        return {
            "conversation_id": conversation_id,
            "assets": st.assets,
            "focal_alarm": st.focal_alarm,
            "turns": st.turns,
        }

    # ------------------------------------------------------------------ discovery / health
    @app.get("/api/tools")
    async def tools() -> dict[str, Any]:
        async with registry.session() as mcp:
            return {
                "servers": [
                    {
                        "name": st.name,
                        "connected": st.connected,
                        "error": st.error,
                        "tools": st.tools,
                        "server_info": st.server_info,
                    }
                    for st in mcp.status.values()
                ],
                "tools": [t.public() for t in mcp.tools.values()],
            }

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        async with registry.session() as mcp:
            servers = {
                st.name: {"connected": st.connected, "tools": st.tools, "error": st.error}
                for st in mcp.status.values()
            }
        rag_status = rag.status()
        ok = servers.get("alarm-management", {}).get("connected") and rag_status["status"] == "ok"
        return {
            "status": "ok" if ok else "degraded",
            "version": __version__,
            "mcp_servers": servers,
            "rag": rag_status,
            "llm": llm.name if llm else "disabled (deterministic grounded composer)",
        }

    @app.get("/api/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------ documents / RAG
    @app.get("/api/documents")
    async def documents() -> dict[str, Any]:
        return {"index": rag.status(), "documents": rag.documents()}

    @app.post("/api/rag/search")
    async def rag_search(req: RagSearchRequest) -> dict[str, Any]:
        try:
            result, trace = await rag.search("debug", req.query, req.filters, req.top_k)
        except RagUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "confidence": result.confidence,
            "reason": result.confidence_reason,
            "citations": [c.model_dump() for c in build_citations(result.hits)],
            "quarantined": result.quarantined,
            "trace": trace.model_dump(),
        }

    # ------------------------------------------------------------------ write operation with explicit approval
    @app.post("/api/work-requests/draft")
    async def draft_work_request(req: WorkRequestDraft) -> dict[str, Any]:
        trace = TraceContext(trace_id=new_id("trace"))
        with trace_scope(trace):
            async with registry.session() as mcp:
                out = await mcp.call(
                    "draft_work_request", req.model_dump(), step="work_request_draft", trace=trace
                )
        if not out.ok:
            raise HTTPException(502 if out.record.status == "error" else 409, out.record.error)
        return {"draft": out.data, "tool_call": out.record.model_dump()}

    @app.post("/api/work-requests/confirm")
    async def confirm_work_request(req: WorkRequestConfirm) -> dict[str, Any]:
        if not req.confirm:
            raise HTTPException(400, "explicit confirmation required")
        trace = TraceContext(trace_id=new_id("trace"))
        with trace_scope(trace):
            async with registry.session() as mcp:
                out = await mcp.call(
                    "submit_work_request",
                    req.model_dump(exclude={"confirm"}),
                    step="work_request_submit",
                    trace=trace,
                    approved=True,
                )
        if not out.ok:
            raise HTTPException(409, out.record.error)
        log.info("work_request_confirmed", extra={"approved_by": req.approved_by})
        return {"result": out.data, "tool_call": out.record.model_dump()}

    # ------------------------------------------------------------------ demo controls (fault injection)
    if s.enable_demo_controls:
        headers = {"Authorization": f"Bearer {s.alarm_api_token.get_secret_value()}"}

        @app.post("/api/demo/faults")
        async def add_fault(req: FaultRequest) -> dict[str, Any]:
            async with httpx.AsyncClient(base_url=s.alarm_api_base_url, timeout=5) as c:
                r = await c.post("/_sim/faults", json=req.model_dump(), headers=headers)
            return r.json()

        @app.delete("/api/demo/faults")
        async def clear_faults() -> dict[str, Any]:
            async with httpx.AsyncClient(base_url=s.alarm_api_base_url, timeout=5) as c:
                r = await c.delete("/_sim/faults", headers=headers)
            return r.json()

        @app.get("/api/demo/faults")
        async def list_faults() -> dict[str, Any]:
            async with httpx.AsyncClient(base_url=s.alarm_api_base_url, timeout=5) as c:
                r = await c.get("/_sim/faults", headers=headers)
            return r.json()

    @app.get("/api/config")
    async def public_config() -> dict[str, Any]:
        return {
            "demo_controls": s.enable_demo_controls,
            "version": __version__,
            "llm": llm.name if llm else None,
            "default_lookback_days": s.default_lookback_days,
        }

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        return JSONResponse(status_code=500, content={"error": "internal error", "type": type(exc).__name__})

    # ------------------------------------------------------------------ static GUI (local dev convenience)
    frontend = Path(s.frontend_dir)
    if s.serve_frontend and (frontend / "index.html").exists():
        app.mount("/static", StaticFiles(directory=frontend), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app
