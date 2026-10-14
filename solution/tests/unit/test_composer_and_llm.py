from types import SimpleNamespace

import pytest

from copilot.composer import compose_markdown, evidence_pack, llm_answer, validate_llm_answer
from copilot.llm import AnthropicProvider, LLMError, LLMResult, build_provider
from copilot.models import ActionItem, Investigation, LikelyCause
from rag.retrieval.retriever import Citation


def _citation(cid: str = "D1") -> Citation:
    return Citation(
        citation_id=cid,
        doc_id="SOP-BFP-001",
        title="BFP SOP",
        doc_type="operating_procedure",
        section="4.2 BFP Suction Pressure Low",
        section_number="4.2",
        revision="7",
        effective_date="2026-01-15",
        trust_level="controlled",
        source_path="x.md",
        chunk_id="c1",
        snippet="Check deaerator level",
        score=0.05,
    )


def _investigation() -> Investigation:
    return Investigation(
        assets=[{"asset_id": "NP-BFP-101", "asset_name": "Boiler Feed Pump 101"}],
        time_window={"start_time": "2026-07-14T00:00:00Z", "end_time": "2026-10-12T00:00:00Z"},
        alarm_summary={
            "total_alarms": 10,
            "kpis": {"recurring_rate": 0.25, "avg_ack_delay_seconds": 300},
            "top_alarms": [
                {
                    "alarm_name": "BFP Suction Pressure Low",
                    "alarm_count": 5,
                    "recurring_rate": 0.3,
                    "max_severity": "high",
                }
            ],
        },
        likely_causes=[
            LikelyCause(cause="Deaerator level", evidence=["corr"], confidence="high", refs=["T3", "D1"])
        ],
        actions=[
            ActionItem(
                action="Reset and restart",
                source="api",
                consistency="conflict",
                note="conflict",
                refs=["T4", "D1"],
            )
        ],
        procedures=["D1"],
    )


def test_compose_markdown_has_sections_and_references() -> None:
    md = compose_markdown(
        "investigate",
        _investigation(),
        [_citation()],
        {"summary": "T2"},
        ["tool X failed"],
        "low confidence",
        notes=["context note"],
    )
    for expected in (
        "Alarm investigation",
        "**Summary**",
        "Likely contributing factors",
        "CONFLICT",
        "follow the controlled procedure",
        "[D1]",
        "[T2]",
        "Evidence gaps",
        "tool X failed",
        "_context note_",
        "SOP-BFP-001 §4.2",
    ):
        assert expected in md


def test_validate_llm_answer_rules() -> None:
    allowed = {"T1", "D1"}
    assert validate_llm_answer("Pump alarms recur [T1]. See SOP [D1].", allowed, True, True) == []
    assert any("unknown" in p for p in validate_llm_answer("x [T9] [D1]", allowed, True, True))
    assert any("no document" in p for p in validate_llm_answer("x [T1]", allowed, True, True))
    assert any(
        "unsafe" in p for p in validate_llm_answer("Bypass the trip now [T1] [D1]", allowed, True, True)
    )
    assert validate_llm_answer("Never bypass the trip [T1] [D1]", allowed, True, True) == []


class FakeLLM:
    name = "fake:model"

    def __init__(self, text: str | None = None, error: bool = False) -> None:
        self.text, self.error, self.prompts = text, error, []

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult:
        self.prompts.append((system, user))
        if self.error:
            raise LLMError("boom")
        return LLMResult(text=self.text or "", model="fake", latency_ms=1.0)


async def test_llm_answer_accept_reject_and_error() -> None:
    pack = evidence_pack("investigate", _investigation(), [_citation()], {}, {"summary": "T1"}, [])
    assert '<document id="D1"' in pack and "</document>" in pack  # documents are delimited as data
    ok, notes, _ = await llm_answer(FakeLLM("Answer [T1] [D1]"), "q", pack, {"T1", "D1"}, True, True)
    assert ok == "Answer [T1] [D1]" and notes == []
    bad, notes, _ = await llm_answer(FakeLLM("Answer without refs"), "q", pack, {"T1", "D1"}, True, True)
    assert bad is None and "grounding check" in notes[0]
    err, notes, _ = await llm_answer(FakeLLM(error=True), "q", pack, {"T1"}, True, True)
    assert err is None and "LLM unavailable" in notes[0]


def test_build_provider_none() -> None:
    assert build_provider("none", "", "m", 1, "low") is None
    with pytest.raises(ValueError):
        build_provider("other", "", "m", 1, "low")


async def test_anthropic_provider_maps_response_and_refusal() -> None:
    provider = AnthropicProvider(api_key="sk-test-not-real", model="claude-opus-5-5")
    calls = {}

    async def create(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Grounded [T1]")],
            stop_reason="end_turn",
            model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    provider._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    res = await provider.complete("sys", "user")
    assert res.text == "Grounded [T1]" and calls["model"] == "claude-opus-5-5"
    assert calls["fallbacks"] == "default" and calls["output_config"] == {"effort": "medium"}

    async def refuse(**_):
        return SimpleNamespace(content=[], stop_reason="refusal", model="claude-opus-5-5", usage=None)

    provider._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=refuse)))
    with pytest.raises(LLMError):
        await provider.complete("sys", "user")
