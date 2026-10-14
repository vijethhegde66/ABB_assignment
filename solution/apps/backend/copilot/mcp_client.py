"""MCP client integration: server connections, tool discovery, schema-aware invocation, tool policy.

The copilot reaches source systems ONLY through this module. Each chat turn
opens an MCP session per configured server (Streamable HTTP, or in-process in
tests), discovers the tools, validates arguments against each tool's
advertised JSON Schema before calling it, enforces the tool policy (write
tools require explicit user approval), applies timeouts and converts every
outcome - success, invalid arguments, unavailable tool, server error - into a
``ToolCallRecord`` for the GUI trace.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx2
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from copilot.models import ToolCallRecord
from platform_common import TraceContext, get_logger

log = get_logger("copilot.mcp")


@dataclass
class McpServerConfig:
    name: str
    url: str | None = None
    auth_token: str = ""
    in_process: Any = None  # an MCPServer instance (tests / embedded mode)
    required: bool = True


@dataclass
class ToolInfo:
    server: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool
    requires_approval: bool

    def public(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "read_only": self.read_only,
            "requires_approval": self.requires_approval,
        }


@dataclass
class ToolOutcome:
    record: ToolCallRecord
    data: dict[str, Any] | None

    @property
    def ok(self) -> bool:
        return self.record.status == "success" and self.data is not None


@dataclass
class ServerStatus:
    name: str
    connected: bool
    error: str | None = None
    tools: int = 0
    server_info: dict[str, Any] = field(default_factory=dict)


class McpSession:
    """Connected view over all configured MCP servers for one request."""

    def __init__(self, timeout_s: float) -> None:
        self.clients: dict[str, Client] = {}
        self.tools: dict[str, ToolInfo] = {}
        self.status: dict[str, ServerStatus] = {}
        self.records: list[ToolCallRecord] = []
        self.timeout_s = timeout_s
        self._ref = 0

    # ------------------------------------------------------------------ discovery
    async def discover(self) -> None:
        for name, client in self.clients.items():
            try:
                cursor: str | None = None
                count = 0
                while True:
                    page = await client.list_tools(cursor=cursor)
                    for t in page.tools:
                        ann = t.annotations
                        read_only = bool(ann and ann.read_only_hint)
                        self.tools[t.name] = ToolInfo(
                            server=name,
                            name=t.name,
                            title=t.title,
                            description=t.description or "",
                            input_schema=t.input_schema,
                            output_schema=t.output_schema,
                            read_only=read_only,
                            requires_approval=not read_only,
                        )
                        count += 1
                    cursor = page.next_cursor
                    if not cursor:
                        break
                self.status[name].tools = count
            except Exception as exc:
                self.status[name].connected = False
                self.status[name].error = f"tool discovery failed: {type(exc).__name__}"
                log.warning("mcp_discovery_failed", extra={"mcp_server": name, "error": str(exc)})

    def has(self, tool: str) -> bool:
        return tool in self.tools

    def _next_ref(self) -> str:
        self._ref += 1
        return f"T{self._ref}"

    # ------------------------------------------------------------------ invocation
    async def call(
        self, tool: str, arguments: dict[str, Any], *, step: str, trace: TraceContext, approved: bool = False
    ) -> ToolOutcome:
        args = {k: v for k, v in arguments.items() if v is not None}
        started_at = datetime.now(UTC).isoformat()
        info = self.tools.get(tool)
        record = ToolCallRecord(
            ref=self._next_ref(),
            step=step,
            server=info.server if info else "?",
            tool=tool,
            arguments=args,
            status="success",
            started_at=started_at,
            trace_id=trace.trace_id,
        )
        self.records.append(record)

        if info is None:
            record.status = "unavailable"
            record.error = {
                "error_code": "TOOL_UNAVAILABLE",
                "message": f"tool '{tool}' is not offered by any connected MCP server",
            }
            log.warning("mcp_tool_unavailable", extra={"mcp_tool": tool})
            return ToolOutcome(record, None)
        if info.requires_approval and not approved:
            record.status = "denied"
            record.error = {
                "error_code": "APPROVAL_REQUIRED",
                "message": f"'{tool}' modifies a source system and requires explicit user approval",
            }
            return ToolOutcome(record, None)

        errors = sorted(Draft202012Validator(info.input_schema).iter_errors(args), key=lambda e: list(e.path))
        if errors:
            record.status = "invalid_arguments"
            record.error = {
                "error_code": "INVALID_ARGUMENT",
                "message": "arguments rejected by client-side schema validation",
                "details": [
                    {"path": "/".join(str(p) for p in e.path) or "(root)", "msg": e.message}
                    for e in errors[:5]
                ],
            }
            log.warning("mcp_invalid_arguments", extra={"mcp_tool": tool, "errors": record.error["details"]})
            return ToolOutcome(record, None)

        client = self.clients[info.server]
        t0 = time.perf_counter()
        attempts = 0
        while True:
            attempts += 1
            try:
                async with asyncio.timeout(self.timeout_s):
                    result = await client.call_tool(tool, args, meta=cast(Any, trace.child().as_mcp_meta()))
                break
            except TimeoutError:
                record.status = "error"
                record.error = {
                    "error_code": "MCP_TIMEOUT",
                    "retryable": True,
                    "message": f"no response from MCP server within {self.timeout_s}s",
                }
                record.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                return ToolOutcome(record, None)
            except Exception as exc:
                if attempts < 2:
                    record.retries += 1
                    await asyncio.sleep(0.2)
                    continue
                record.status = "error"
                record.error = {
                    "error_code": "MCP_TRANSPORT_ERROR",
                    "retryable": True,
                    "message": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
                record.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                return ToolOutcome(record, None)
        record.duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        if result.is_error:
            record.status = "error"
            record.error = parse_tool_error(_text_of(result))
            record.api_calls = record.error.pop("api_calls", []) or []
            if record.error.get("error_code") == "INVALID_ARGUMENT":
                record.status = "invalid_arguments"
            log.info("mcp_tool_error", extra={"mcp_tool": tool, "error_code": record.error.get("error_code")})
            return ToolOutcome(record, None)

        data = result.structured_content
        if data is None:
            try:
                data = json.loads(_text_of(result))
            except ValueError:
                data = {"text": _text_of(result)}
        trace_info = data.get("trace") if isinstance(data, dict) else None
        if isinstance(trace_info, dict):
            record.api_calls = trace_info.get("api_calls", [])
        record.result = data
        log.info(
            "mcp_tool_ok",
            extra={"mcp_tool": tool, "duration_ms": record.duration_ms, "api_calls": len(record.api_calls)},
        )
        return ToolOutcome(record, data)


def _text_of(result: Any) -> str:
    parts = [getattr(c, "text", "") for c in (result.content or [])]
    return "\n".join(p for p in parts if p)


def parse_tool_error(text: str) -> dict[str, Any]:
    """Tool errors carry a JSON payload after the SDK prefix; fall back to a generic error otherwise."""
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
            if isinstance(payload, dict) and "error_code" in payload:
                return payload
        except ValueError:
            pass
    if "validation error" in text.lower():
        return {"error_code": "INVALID_ARGUMENT", "message": text.split("\n", 1)[-1][:400]}
    if text.lower().startswith("unknown tool"):
        return {"error_code": "TOOL_UNAVAILABLE", "message": text[:200]}
    return {"error_code": "TOOL_ERROR", "message": text[:400]}


class McpToolRegistry:
    """Owns server configuration and opens per-request sessions."""

    def __init__(
        self, servers: list[McpServerConfig], *, call_timeout_s: float = 45.0, connect_timeout_s: float = 10.0
    ) -> None:
        self.servers = servers
        self.call_timeout_s = call_timeout_s
        self.connect_timeout_s = connect_timeout_s

    def _client(self, cfg: McpServerConfig) -> Client:
        if cfg.in_process is not None:
            return Client(cfg.in_process, read_timeout_seconds=self.call_timeout_s)
        headers = {"Authorization": f"Bearer {cfg.auth_token}"} if cfg.auth_token else {}
        http = httpx2.AsyncClient(
            headers=headers, timeout=httpx2.Timeout(self.call_timeout_s, connect=self.connect_timeout_s)
        )
        assert cfg.url
        return Client(
            streamable_http_client(cfg.url, http_client=http), read_timeout_seconds=self.call_timeout_s
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[McpSession]:
        session = McpSession(self.call_timeout_s)
        async with AsyncExitStack() as stack:
            for cfg in self.servers:
                session.status[cfg.name] = ServerStatus(cfg.name, connected=False)
                try:
                    async with asyncio.timeout(self.connect_timeout_s):
                        client = await stack.enter_async_context(self._client(cfg))
                except Exception as exc:
                    msg = f"{type(exc).__name__}: {str(exc)[:160]}"
                    session.status[cfg.name].error = msg
                    log.warning("mcp_connect_failed", extra={"mcp_server": cfg.name, "error": msg})
                    continue
                session.clients[cfg.name] = client
                session.status[cfg.name].connected = True
                info = client.server_info
                if info is not None:
                    session.status[cfg.name].server_info = {"name": info.name, "version": info.version}
            await session.discover()
            yield session
