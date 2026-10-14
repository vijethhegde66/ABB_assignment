"""MCP server tool tests: registration, discovery, schemas, validation, auth, pagination, timeouts,
retries, error mapping and trace propagation - through a real MCP client session."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client
from pydantic import SecretStr
from starlette.testclient import TestClient

from alarm_mcp import build_server
from alarm_mcp.app import create_http_app
from alarm_mcp.config import McpServerSettings
from tests.conftest import END, make_api

EXPECTED_TOOLS = {
    "search_assets",
    "get_asset_metadata",
    "get_alarms",
    "get_alarm_details",
    "summarize_alarms",
    "get_alarm_trends",
    "correlate_alarms",
    "analyze_alarm_floods",
    "find_rationalization_candidates",
    "score_alarm_priority",
    "rank_alarm_priorities",
    "recommend_operator_actions",
    "calculate_kpi",
    "list_kpi_definitions",
}
WINDOW = {"end_time": END.isoformat().replace("+00:00", "Z")}


def err(result: Any) -> dict[str, Any]:
    text = result.content[0].text
    return json.loads(text[text.index("{") :])


async def test_tool_registration_and_discovery(alarm_server) -> None:
    async with Client(alarm_server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS
        for t in tools.values():
            assert t.description and len(t.description) > 30
            assert t.input_schema["type"] == "object"
            assert t.output_schema is not None and "trace" in t.output_schema["properties"]
            assert t.annotations.read_only_hint is True
        assert tools["search_assets"].input_schema["required"] == ["query"]
        assert c.server_info.name == "alarm-management"


async def test_search_then_metadata_chain(alarm_server) -> None:
    async with Client(alarm_server) as c:
        s = (await c.call_tool("search_assets", {"query": "Boiler Feed Pump 101"})).structured_content
        assert s["best_match"]["asset_id"] == "NP-BFP-101" and s["ambiguous"] is False
        m = (
            await c.call_tool("get_asset_metadata", {"asset_id": s["best_match"]["asset_id"]})
        ).structured_content
        assert {r["asset_id"] for r in m["related_assets"]} >= {"NP-DEA-001", "NP-LOS-101"}
        assert m["trace"]["api_calls"][0]["path"] == "/assets/NP-BFP-101/metadata"


@pytest.mark.parametrize(
    ("tool", "args", "fragment"),
    [
        ("get_asset_metadata", {"asset_id": "bad id!"}, "pattern"),
        ("search_assets", {"query": ""}, "at least 1 character"),
        ("get_alarms", {"asset_id": "NP-BFP-101", "page_size": 9999}, "less than or equal"),
        ("score_alarm_priority", {"alarm_id": "1234"}, "pattern"),
        ("correlate_alarms", {"asset_ids": []}, "at least 1 item"),
        ("calculate_kpi", {"calculation_type": "drop_tables"}, "Input should be"),
    ],
)
async def test_schema_validation_rejects_bad_input(
    alarm_server, tool: str, args: dict, fragment: str
) -> None:
    async with Client(alarm_server) as c:
        r = await c.call_tool(tool, args)
        assert r.is_error and fragment in r.content[0].text


async def test_cross_field_validation_returns_invalid_argument(alarm_server) -> None:
    async with Client(alarm_server) as c:
        r = await c.call_tool("get_alarms", {})
        assert r.is_error and err(r)["error_code"] == "INVALID_ARGUMENT"
        r = await c.call_tool(
            "summarize_alarms",
            {
                "asset_ids": ["NP-BFP-101"],
                "start_time": "2026-10-01T00:00:00Z",
                "end_time": "2026-09-01T00:00:00Z",
            },
        )
        assert err(r)["error_code"] == "INVALID_ARGUMENT"


async def test_not_found_is_mapped(alarm_server) -> None:
    async with Client(alarm_server) as c:
        e = err(await c.call_tool("get_asset_metadata", {"asset_id": "NP-XXX-999"}))
        assert e["error_code"] == "NOT_FOUND" and e["status_code"] == 404 and e["retryable"] is False
        assert e["api_calls"][0]["outcome"] == "error"


async def test_authentication_failure_is_mapped(sim_app) -> None:
    api = make_api(sim_app, token=SecretStr("wrong-token"))
    async with Client(build_server(api)) as c:
        e = err(await c.call_tool("search_assets", {"query": "pump"}))
        assert e["error_code"] == "AUTHENTICATION_FAILED" and "wrong-token" not in json.dumps(e)


async def test_pagination_fetch_all_pages(alarm_server) -> None:
    async with Client(alarm_server) as c:
        r = (
            await c.call_tool(
                "get_alarms",
                {
                    "asset_id": "NP-BFP-101",
                    "page_size": 25,
                    "fetch_all_pages": True,
                    "max_pages": 3,
                    **WINDOW,
                },
            )
        ).structured_content
        assert (
            r["pagination"]["pages_fetched"] == 3 and len(r["alarms"]) == 75 and r["pagination"]["truncated"]
        )
        assert [c["params"]["page"] for c in r["trace"]["api_calls"]] == [1, 2, 3]


async def test_retry_on_transient_failure(alarm_server, sim_app) -> None:
    sim_app.state.faults.rules.append(
        {
            "path_prefix": "/alarms/correlation",
            "mode": "error",
            "status_code": 503,
            "delay_ms": 0,
            "remaining": 2,
        }
    )
    async with Client(alarm_server) as c:
        r = await c.call_tool("correlate_alarms", {"asset_ids": ["NP-BFP-101"], **WINDOW})
        call = r.structured_content["trace"]["api_calls"][0]
        assert not r.is_error and call["attempts"] == 3 and call["retries"] == ["http_503", "http_503"]


async def test_timeout_maps_to_upstream_timeout(sim_app) -> None:
    sim_app.state.faults.rules.append(
        {
            "path_prefix": "/alarms/trends",
            "mode": "latency",
            "status_code": 200,
            "delay_ms": 1500,
            "remaining": None,
        }
    )
    api = make_api(sim_app, timeout_seconds=0.2, max_retries=1)
    async with Client(build_server(api)) as c:
        e = err(await c.call_tool("get_alarm_trends", {"asset_ids": ["NP-BFP-101"], **WINDOW}))
        assert e["error_code"] == "UPSTREAM_TIMEOUT" and e["retryable"] and e["attempts"] == 2


async def test_trace_metadata_propagates_to_alarm_api(alarm_server, sim_app) -> None:
    async with Client(alarm_server) as c:
        r = await c.call_tool(
            "search_assets",
            {"query": "compressor"},
            meta={"trace_id": "trace-from-copilot", "request_id": "req-1"},
        )
        assert r.structured_content["trace"]["trace_id"] == "trace-from-copilot"
    entry = next(e for e in sim_app.state.request_log if e["path"] == "/assets/search")
    assert entry["trace_id"] == "trace-from-copilot"
    assert entry["client_id"] == "alarm-mcp-server" and entry["metadata_tag"] == "asset-search"


async def test_rank_priorities_partial_failure(alarm_server) -> None:
    async with Client(alarm_server) as c:
        active = (
            await c.call_tool("get_alarms", {"site": "EastRefinery", "status": "active"})
        ).structured_content
        ids = [*[a["alarm_id"] for a in active["alarms"]][:3], "ALM-9999999"]
        r = (await c.call_tool("rank_alarm_priorities", {"alarm_ids": ids})).structured_content
        assert len(r["ranked"]) == 3 and r["failed"][0]["error_code"] == "NOT_FOUND"
        scores = [x["priority_score"] for x in r["ranked"]]
        assert scores == sorted(scores, reverse=True) and r["ranked"][0]["rank"] == 1


async def test_recommendations_and_kpi_chain(alarm_server) -> None:
    async with Client(alarm_server) as c:
        active = (
            await c.call_tool("get_alarms", {"asset_id": "NP-BFP-102", "status": "active"})
        ).structured_content
        rec = (
            await c.call_tool("recommend_operator_actions", {"alarm_id": active["alarms"][0]["alarm_id"]})
        ).structured_content
        assert rec["recommendations"] and rec["related_assets_to_check"] and rec["historical_pattern"]
        kpi = (
            await c.call_tool(
                "calculate_kpi", {"calculation_type": "alarm_flood_index", "unit": "Unit 2", **WINDOW}
            )
        ).structured_content
        assert [c["path"] for c in kpi["trace"]["api_calls"]] == [
            "/calculation-code/generate",
            "/calculation-code/execute",
        ]
        assert kpi["result"]["unit"].startswith("%")


async def test_all_tools_execute_successfully(alarm_server) -> None:
    async with Client(alarm_server) as c:
        calls = {
            "get_alarm_details": {"alarm_id": "ALM-0000010"},
            "summarize_alarms": {"site": "NorthPlant", **WINDOW},
            "get_alarm_trends": {"unit": "Unit 3", **WINDOW},
            "analyze_alarm_floods": {"unit": "Unit 2", **WINDOW},
            "find_rationalization_candidates": {"site": "NorthPlant", **WINDOW},
            "score_alarm_priority": {"alarm_id": "ALM-0000010"},
            "list_kpi_definitions": {},
        }
        for tool, args in calls.items():
            r = await c.call_tool(tool, args)
            assert not r.is_error, (tool, r.content[0].text[:300])


def test_http_transport_requires_bearer_token(api) -> None:
    app = create_http_app(
        build_server(api), McpServerSettings(auth_token=SecretStr("mcp-secret"), host="0.0.0.0")
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Bearer"
        wrong = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = client.post(
            "/mcp",
            headers={"Authorization": "Bearer mcp-secret", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
        )
        assert ok.status_code != 401
