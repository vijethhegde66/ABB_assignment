"""Mandatory end-to-end acceptance scenario over real HTTP:

GUI/backend request -> copilot orchestration -> MCP client (Streamable HTTP, bearer auth) -> candidate MCP server
-> Alarm Management API simulator, combined with document RAG -> grounded response with citations and MCP trace.
"""

from __future__ import annotations

import re

import httpx
import pytest

pytestmark = pytest.mark.e2e

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify "
    "likely contributing factors, retrieve the relevant operating procedure, and provide recommended "
    "actions with source evidence."
)


def test_acceptance_scenario_over_http(http_stack) -> None:
    with httpx.Client(base_url=http_stack.copilot_url, timeout=60) as c:
        health = c.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["mcp_servers"]["alarm-management"]["tools"] == 14

        r = c.post("/api/chat", json={"message": ACCEPTANCE})
        assert r.status_code == 200
        body = r.json()

    # 1. asset resolution through an MCP tool
    trace = body["tool_trace"]
    assert trace[0]["tool"] == "search_assets" and trace[0]["status"] == "success"
    asset_id = trace[0]["result"]["best_match"]["asset_id"]
    assert asset_id == "NP-BFP-101"
    # 2. multi-step Alarm Management API chaining through MCP (outputs feed later inputs)
    names = [t["tool"] for t in trace]
    for tool in (
        "get_asset_metadata",
        "summarize_alarms",
        "get_alarm_trends",
        "get_alarms",
        "correlate_alarms",
        "score_alarm_priority",
        "recommend_operator_actions",
        "list_work_orders",
    ):
        assert tool in names, tool
    assert all(t["status"] == "success" for t in trace)
    corr = next(t for t in trace if t["tool"] == "correlate_alarms")
    assert asset_id in corr["arguments"]["asset_ids"]
    # 3. document retrieval through RAG
    purposes = {q["purpose"] for q in body["retrieval"]}
    assert {"applicable_procedure", "troubleshooting", "cause_evidence", "recommendation_check"} <= purposes
    # 4. combined reasoning: causes backed by correlation (MCP) + documents (RAG) + work orders (2nd MCP server)
    causes = body["investigation"]["likely_causes"]
    top = causes[0]
    assert "Deaerator" in top["cause"] and top["confidence"] == "high"
    assert any(r.startswith("T") for r in top["refs"]) and any(r.startswith("D") for r in top["refs"])
    # 5. citations
    cited = {(c["doc_id"], c["section_number"]) for c in body["citations"]}
    assert ("SOP-BFP-001", "4.2") in cited
    assert any(d == "TG-BFP-003" for d, _ in cited)
    # 6. grounded answer: every reference in the answer resolves to a tool call or a citation
    refs = set(re.findall(r"\b([TD]\d+)\b", body["answer"]["markdown"]))
    assert refs and refs <= {t["ref"] for t in trace} | {c["citation_id"] for c in body["citations"]}
    assert body["status"] == "complete" and body["answer"]["grounded"]
    # 7. MCP execution trace with propagated trace id down to the source system
    with httpx.Client(base_url=http_stack.sim_url, timeout=10) as sim:
        log = sim.get(
            "/_sim/requests",
            params={"trace_id": body["trace_id"], "limit": 500},
            headers={"Authorization": f"Bearer {http_stack.sim_token}"},
        ).json()["requests"]
    assert len(log) >= len(trace) - 1  # every alarm-API-backed tool call reached the simulator
    assert {e["client_id"] for e in log} == {"alarm-mcp-server"}
    assert all(e["status_code"] == 200 for e in log)


def test_degraded_scenario_over_http(http_stack) -> None:
    with httpx.Client(base_url=http_stack.copilot_url, timeout=60) as c:
        c.post(
            "/api/demo/faults", json={"path_prefix": "/recommendations", "mode": "error", "status_code": 500}
        )
        try:
            body = c.post(
                "/api/chat",
                json={
                    "message": "Show active critical alarms for Boiler Feed Pump 102 and "
                    "recommend immediate actions."
                },
            ).json()
        finally:
            c.delete("/api/demo/faults")
    assert body["status"] == "partial"
    failed = next(t for t in body["tool_trace"] if t["tool"] == "recommend_operator_actions")
    assert failed["status"] == "error" and failed["error"]["status_code"] == 500
    assert any("recommend_operator_actions" in w for w in body["warnings"])
    assert any(a["source"] == "procedure" for a in body["investigation"]["actions"])  # documents still guide


def test_mcp_endpoint_rejects_unauthenticated_clients(http_stack) -> None:
    r = httpx.post(http_stack.mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
