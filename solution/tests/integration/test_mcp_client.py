"""MCP client integration tests: connectivity, discovery, invocation, invalid args, missing tools,
partial failure (one server down), approval policy and client-side timeouts."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

from copilot.mcp_client import McpServerConfig, McpToolRegistry
from platform_common import TraceContext

TRACE = TraceContext(trace_id="trace-test")


async def test_connect_and_discover_two_servers(registry: McpToolRegistry) -> None:
    async with registry.session() as s:
        assert s.status["alarm-management"].connected and s.status["alarm-management"].tools == 14
        assert s.status["work-orders"].connected and s.status["work-orders"].tools == 3
        assert s.tools["search_assets"].server == "alarm-management"
        assert s.tools["submit_work_request"].requires_approval
        assert not s.tools["search_assets"].requires_approval


async def test_invoke_success_records_trace(registry: McpToolRegistry) -> None:
    async with registry.session() as s:
        out = await s.call(
            "search_assets", {"query": "Boiler Feed Pump 102", "site": None}, step="t", trace=TRACE
        )
        assert out.ok and out.data["best_match"]["asset_id"] == "NP-BFP-102"
        rec = out.record
        assert (
            rec.ref == "T1" and rec.status == "success" and rec.arguments == {"query": "Boiler Feed Pump 102"}
        )
        assert rec.api_calls[0]["trace_id"] == "trace-test" and rec.result is not None


async def test_invalid_arguments_caught_client_side(registry: McpToolRegistry, sim_app) -> None:
    async with registry.session() as s:
        out = await s.call("get_asset_metadata", {"asset_id": "lower case"}, step="t", trace=TRACE)
        assert out.record.status == "invalid_arguments" and out.data is None
        assert out.record.error["details"][0]["path"] == "asset_id"
        out = await s.call(
            "search_assets", {"query": "x", "unknown_field": 1, "limit": "ten"}, step="t", trace=TRACE
        )
        assert out.record.status == "invalid_arguments"
    assert not sim_app.state.request_log  # nothing reached the API


async def test_server_side_rejection_is_mapped(registry: McpToolRegistry) -> None:
    async with registry.session() as s:
        out = await s.call(
            "get_alarms", {}, step="t", trace=TRACE
        )  # passes schema, fails cross-field validation
        assert (
            out.record.status == "invalid_arguments" and out.record.error["error_code"] == "INVALID_ARGUMENT"
        )
        out = await s.call("get_alarm_details", {"alarm_id": "ALM-9999999"}, step="t", trace=TRACE)
        assert out.record.status == "error" and out.record.error["error_code"] == "NOT_FOUND"
        assert out.record.api_calls and out.record.api_calls[0]["status_code"] == 404


async def test_missing_tool_is_unavailable(registry: McpToolRegistry) -> None:
    async with registry.session() as s:
        out = await s.call("delete_all_alarms", {}, step="t", trace=TRACE)
        assert out.record.status == "unavailable" and out.record.error["error_code"] == "TOOL_UNAVAILABLE"


async def test_write_tool_requires_explicit_approval(registry: McpToolRegistry) -> None:
    async with registry.session() as s:
        draft = await s.call(
            "draft_work_request",
            {"asset_id": "NP-BFP-101", "title": "Inspect pump", "description": "Recurring alarms"},
            step="t",
            trace=TRACE,
        )
        assert draft.ok
        args = {
            "draft_id": draft.data["draft_id"],
            "approval_token": draft.data["approval_token"],
            "approved_by": "Operator A",
        }
        denied = await s.call("submit_work_request", args, step="t", trace=TRACE)
        assert denied.record.status == "denied" and denied.record.error["error_code"] == "APPROVAL_REQUIRED"
        approved = await s.call("submit_work_request", args, step="t", trace=TRACE, approved=True)
        assert approved.ok and approved.data["work_order"]["requested_by"] == "Operator A"


async def test_partial_failure_one_server_unreachable(alarm_server) -> None:
    reg = McpToolRegistry(
        [
            McpServerConfig("alarm-management", in_process=alarm_server),
            McpServerConfig("work-orders", url="http://127.0.0.1:9/mcp", required=False),
        ],
        call_timeout_s=5,
        connect_timeout_s=3,
    )
    async with reg.session() as s:
        assert s.status["alarm-management"].connected
        assert not s.status["work-orders"].connected and s.status["work-orders"].error
        assert s.has("search_assets") and not s.has("list_work_orders")
        assert (await s.call("search_assets", {"query": "pump"}, step="t", trace=TRACE)).ok
        assert (
            await s.call("list_work_orders", {"asset_ids": ["NP-BFP-101"]}, step="t", trace=TRACE)
        ).record.status == "unavailable"


async def test_client_timeout() -> None:
    slow = MCPServer("slow")

    @slow.tool(description="sleeps")
    async def nap(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    reg = McpToolRegistry([McpServerConfig("slow", in_process=slow)], call_timeout_s=0.3)
    async with reg.session() as s:
        s.tools["nap"].requires_approval = False  # unannotated tool; allow for the test
        out = await s.call("nap", {"seconds": 2}, step="t", trace=TRACE)
        assert out.record.status == "error" and out.record.error["error_code"] == "MCP_TIMEOUT"
