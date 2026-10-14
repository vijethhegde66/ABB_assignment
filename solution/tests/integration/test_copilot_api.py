"""Copilot HTTP API tests (FastAPI TestClient over the in-process MCP stack)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.config import CopilotSettings


def make_client(registry, rag_service, **env) -> TestClient:
    settings = CopilotSettings(_env_file=None, COPILOT_REFERENCE_TIME="2026-10-12T00:00:00Z", **env)
    return TestClient(create_app(settings, registry=registry, rag=rag_service))


def test_chat_endpoint_returns_structured_response(registry, rag_service) -> None:
    c = make_client(registry, rag_service)
    r = c.post(
        "/api/chat",
        json={
            "message": "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions."
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete" and body["citations"] and body["tool_trace"]
    assert body["tool_trace"][0]["api_calls"][0]["trace_id"] == body["trace_id"]
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    follow = c.post(
        "/api/chat",
        json={
            "message": "Which operating procedure applies to this alarm?",
            "conversation_id": body["conversation_id"],
        },
    ).json()
    assert (
        follow["investigation"]["focal_alarm"]["alarm_id"] == body["investigation"]["focal_alarm"]["alarm_id"]
    )
    conv = c.get(f"/api/conversations/{body['conversation_id']}").json()
    assert len(conv["turns"]) == 4


def test_chat_input_validation(registry, rag_service) -> None:
    c = make_client(registry, rag_service)
    assert c.post("/api/chat", json={"message": ""}).status_code == 422
    assert c.post("/api/chat", json={"message": "x" * 2001}).status_code == 422
    assert c.post("/api/chat", json={"message": "hi", "conversation_id": "../../etc"}).status_code == 422
    assert c.get("/api/conversations/unknown").status_code == 404


def test_tools_health_documents_and_rag_search(registry, rag_service) -> None:
    c = make_client(registry, rag_service)
    tools = c.get("/api/tools").json()
    assert {s["name"] for s in tools["servers"] if s["connected"]} == {"alarm-management", "work-orders"}
    assert len(tools["tools"]) == 17
    health = c.get("/api/health").json()
    assert health["status"] == "ok" and health["rag"]["documents"] == 11
    assert c.get("/api/documents").json()["documents"]
    rs = c.post("/api/rag/search", json={"query": "motor trip restart", "top_k": 2}).json()
    assert rs["citations"] and rs["confidence"] in {"high", "medium"}
    assert c.get("/api/config").json()["demo_controls"] is False
    assert c.post("/api/demo/faults", json={"path_prefix": "/alarms"}).status_code in {404, 405}


def test_work_request_requires_explicit_confirmation(registry, rag_service) -> None:
    c = make_client(registry, rag_service)
    draft = c.post(
        "/api/work-requests/draft",
        json={
            "asset_id": "NP-DEA-001",
            "title": "Stroke test LCV-100",
            "description": "Recurring deaerator level dips",
        },
    ).json()["draft"]
    payload = {
        "draft_id": draft["draft_id"],
        "approval_token": draft["approval_token"],
        "approved_by": "Op A",
    }
    assert c.post("/api/work-requests/confirm", json={**payload, "confirm": False}).status_code == 400
    assert (
        c.post(
            "/api/work-requests/confirm",
            json={**payload, "approval_token": "1.forged-token-x", "confirm": True},
        ).status_code
        == 409
    )
    ok = c.post("/api/work-requests/confirm", json={**payload, "confirm": True})
    assert ok.status_code == 200 and ok.json()["result"]["work_order"]["requested_by"] == "Op A"
