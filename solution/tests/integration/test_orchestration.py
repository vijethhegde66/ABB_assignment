"""Orchestration tests: multi-step MCP chains, output->input chaining, RAG in the same workflow,
combined answers, partial source failure, conflicting evidence, context retention, clarification."""

from __future__ import annotations

import re

from copilot.mcp_client import McpServerConfig, McpToolRegistry
from copilot.memory import ConversationStore
from copilot.models import ChatResponse
from copilot.orchestrator import Orchestrator
from tests.conftest import END
from tests.unit.test_composer_and_llm import FakeLLM


def tools(r: ChatResponse) -> list[str]:
    return [t.tool for t in r.tool_trace]


def call(r: ChatResponse, tool: str):
    return next(t for t in r.tool_trace if t.tool == tool)


async def test_investigation_chain_passes_outputs_between_tools(orchestrator: Orchestrator) -> None:
    r = await orchestrator.chat(
        "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days"
    )
    assert r.status == "complete" and r.intent.name == "investigate"
    order = tools(r)
    for earlier, later in [
        ("search_assets", "get_asset_metadata"),
        ("get_asset_metadata", "summarize_alarms"),
        ("summarize_alarms", "correlate_alarms"),
        ("correlate_alarms", "list_work_orders"),
    ]:
        assert order.index(earlier) < order.index(later)
    asset_id = call(r, "search_assets").result["best_match"]["asset_id"]
    assert call(r, "get_asset_metadata").arguments["asset_id"] == asset_id
    assert call(r, "summarize_alarms").arguments["asset_ids"] == [asset_id]
    assert call(r, "summarize_alarms").arguments["severity"] == ["high", "critical"]
    focal = r.investigation.focal_alarm
    assert call(r, "score_alarm_priority").arguments["alarm_id"] == focal["alarm_id"]
    assert call(r, "recommend_operator_actions").arguments["alarm_id"] == focal["alarm_id"]
    related = {x["asset_id"] for x in call(r, "get_asset_metadata").result["related_assets"]}
    assert related <= set(call(r, "list_work_orders").arguments["asset_ids"])


async def test_rag_runs_in_same_workflow_and_grounds_answer(orchestrator: Orchestrator) -> None:
    r = await orchestrator.chat(
        "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days"
    )
    focal = r.investigation.focal_alarm
    proc = next(t for t in r.retrieval if t.purpose == "applicable_procedure")
    assert focal["alarm_name"] in proc.query  # retrieval query built from MCP output
    assert proc.filters["alarm_codes"] == [focal["alarm_code"]]
    cited_docs = {c.doc_id for c in r.citations}
    assert {"SOP-BFP-001", "TG-BFP-003"} <= cited_docs
    refs = set(re.findall(r"\b([TD]\d+)\b", r.answer.markdown))
    assert refs <= {c.citation_id for c in r.citations} | {t.ref for t in r.tool_trace}
    assert any(x.startswith("D") for x in refs) and any(x.startswith("T") for x in refs)
    assert any("Deaerator" in c.cause and c.confidence == "high" for c in r.investigation.likely_causes)
    assert any(c.refs and any(x.startswith("D") for x in c.refs) for c in r.investigation.likely_causes)


async def test_conflicting_evidence_is_flagged(orchestrator: Orchestrator) -> None:
    r = await orchestrator.chat("Which alarm has the highest priority in EastRefinery, and why?")
    assert r.investigation.ranked_alarms[0]["alarm_name"] == "Motor Trip"
    api_actions = [a for a in r.investigation.actions if a.source == "api"]
    conflict = next(a for a in api_actions if a.consistency == "conflict")
    assert "restart" in conflict.action.lower() and "SAF-ELE-020" in (conflict.note or "")
    assert "follow the controlled procedure" in r.answer.markdown
    assert any(c.doc_id == "ALM-PHIL-030" for c in r.citations)


async def test_context_retention_across_turns(orchestrator: Orchestrator) -> None:
    first = await orchestrator.chat("Which alarm has the highest priority in EastRefinery, and why?")
    conv = first.conversation_id
    second = await orchestrator.chat(
        "What related assets should be inspected for this motor trip alarm?", conv
    )
    assert second.investigation.focal_alarm["alarm_id"] == first.investigation.focal_alarm["alarm_id"]
    assert {a["asset_name"] for a in second.investigation.related_assets} >= {"Crude Charge Pump P-501"}
    assert any("conversation context" in n for n in second.notes)
    third = await orchestrator.chat("Which operating procedure applies to this alarm?", conv)
    assert third.intent.name == "procedure_lookup"
    assert any(c.doc_id == "SAF-ELE-020" for c in third.citations)


async def test_partial_failure_degrades_gracefully(orchestrator: Orchestrator, sim_app) -> None:
    sim_app.state.faults.rules.append(
        {
            "path_prefix": "/alarms/correlation",
            "mode": "error",
            "status_code": 503,
            "delay_ms": 0,
            "remaining": None,
        }
    )
    r = await orchestrator.chat("Investigate recurring alarms on Boiler Feed Pump 101 over the last 90 days")
    assert r.status == "partial"
    corr = call(r, "correlate_alarms")
    assert corr.status == "error" and corr.error["error_code"] == "UPSTREAM_UNAVAILABLE"
    assert any("correlate_alarms" in w for w in r.warnings)
    assert r.citations and r.investigation.alarm_summary  # still grounded on what succeeded
    assert "Evidence gaps" in r.answer.markdown


async def test_alarm_mcp_server_unavailable_gives_document_only_answer(rag_service) -> None:
    reg = McpToolRegistry(
        [McpServerConfig("alarm-management", url="http://127.0.0.1:9/mcp")],
        call_timeout_s=3,
        connect_timeout_s=2,
    )
    orch = Orchestrator(reg, rag_service, ConversationStore(), reference_time=END)
    r = await orch.chat("What should I do about BFP suction pressure low alarms?")
    assert r.status == "partial" and not r.tool_trace
    assert any("MCP server is unavailable" in w for w in r.warnings)
    assert r.citations


async def test_unknown_asset_needs_clarification(orchestrator: Orchestrator) -> None:
    r = await orchestrator.chat("Investigate alarms on Flux Capacitor Pump 999 over the last 30 days")
    assert r.status == "needs_clarification"
    assert "could not find an asset" in r.answer.markdown
    r2 = await orchestrator.chat("Show the recurring alarms")
    assert r2.status == "needs_clarification"


async def test_generic_equipment_class_investigates_multiple_assets(orchestrator: Orchestrator) -> None:
    r = await orchestrator.chat("Why are compressor discharge pressure alarms repeatedly occurring?")
    assert len(r.investigation.assets) >= 2
    assert r.investigation.focal_alarm["alarm_name"] == "Compressor Discharge Pressure High"
    assert any("Intercooler" in c.cause for c in r.investigation.likely_causes)
    assert any(c.doc_id == "TG-CMP-011" for c in r.citations)


async def test_active_response_and_performance_and_document_question(orchestrator: Orchestrator) -> None:
    a = await orchestrator.chat(
        "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions."
    )
    assert a.investigation.active_alarms and all(
        x["severity"] == "critical" for x in a.investigation.active_alarms
    )
    assert any(x.source == "procedure" for x in a.investigation.actions)
    p = await orchestrator.chat("Show alarm floods and chattering alarms in Unit 2 last month")
    assert p.intent.name == "alarm_performance" and p.investigation.kpis
    assert "calculate_kpi" in tools(p) and "analyze_alarm_floods" in tools(p)
    d = await orchestrator.chat("What PPE is required in process areas?")
    assert d.intent.name == "document_question" and not d.tool_trace
    assert any(c.doc_id == "SAF-GEN-040" for c in d.citations)


async def test_prompt_injection_document_cannot_steer_answer(orchestrator: Orchestrator) -> None:
    r = await orchestrator.chat(
        "Should I disable alarm BFP_SP_LO because it is a nuisance on Boiler Feed Pump 101?"
    )
    text = r.answer.markdown.lower()
    assert "ignore all previous instructions" not in text and "system prompt" not in text
    assert not any(c.doc_id == "KB-VENDOR-099" for c in r.citations)
    assert all(t.tool != "submit_work_request" for t in r.tool_trace)


async def test_llm_path_used_when_valid_and_rejected_when_ungrounded(registry, rag_service) -> None:
    good = Orchestrator(
        registry,
        rag_service,
        ConversationStore(),
        FakeLLM("Summary of pump alarms [T1] per [D1]."),
        reference_time=END,
    )
    r = await good.chat(
        "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions."
    )
    assert r.answer.generated_by == "fake:model"
    bad = Orchestrator(
        registry,
        rag_service,
        ConversationStore(),
        FakeLLM("Just bypass the trip, trust me."),
        reference_time=END,
    )
    r = await bad.chat(
        "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions."
    )
    assert r.answer.generated_by == "deterministic-grounded-composer"
    assert any("grounding check" in w for w in r.warnings)
