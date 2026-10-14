from copilot.evidence import (
    build_actions,
    cause_candidates,
    check_recommendation,
    equipment_classes,
    matches_terms,
    procedure_steps,
    select_focal_alarm,
)
from copilot.models import ActionItem
from rag.retrieval.retriever import RetrievedChunk


def _chunk(doc_id: str, text: str, trust: str = "controlled", heading: str = "h") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{doc_id}#1",
        doc_id=doc_id,
        text=text,
        heading_path=[heading],
        score=1.0,
        dense_score=0.5,
        bm25_score=5.0,
        matched_terms=[],
        metadata={"trust_level": trust, "heading": heading, "doc_type": "operating_procedure"},
    )


def _alarm(
    aid: str, name: str, sev: str = "high", status: str = "cleared", start: str = "2026-10-01T00:00:00Z"
):
    return {
        "alarm_id": aid,
        "alarm_name": name,
        "severity": sev,
        "status": status,
        "start_time": start,
        "asset_id": "A-1",
        "alarm_code": "X",
    }


def test_matches_terms_prefers_specific_terms() -> None:
    assert matches_terms("Motor Trip", ["motor trip", "trip"])
    assert not matches_terms("Motor Current High", ["motor trip", "trip"])
    assert matches_terms("anything", [])


def test_focal_alarm_prefers_terms_then_active_then_severity() -> None:
    alarms = [
        _alarm("ALM-1", "Motor Current High", "medium", "active"),
        _alarm("ALM-2", "Motor Trip", "critical", "cleared"),
        _alarm("ALM-3", "Motor Trip", "critical", "active"),
    ]
    assert select_focal_alarm(alarms, terms=["motor trip"])["alarm_id"] == "ALM-3"
    assert select_focal_alarm(alarms)["alarm_id"] == "ALM-3"
    assert select_focal_alarm([]) is None


def test_focal_alarm_uses_recurrence_for_history() -> None:
    alarms = [_alarm("ALM-1", "Rare Critical", "critical"), _alarm("ALM-2", "Frequent High", "high")]
    groups = [
        {"group": {"alarm_name": "Frequent High"}, "alarm_count": 30},
        {"group": {"alarm_name": "Rare Critical"}, "alarm_count": 1},
    ]
    focal = select_focal_alarm(alarms, prefer_active=False, summary_groups=groups)
    assert focal["alarm_id"] == "ALM-2"


def test_equipment_classes_include_related_assets() -> None:
    assets = [
        {
            "asset_type": "boiler_feed_pump",
            "related_assets": [{"asset_type": "deaerator"}, {"asset_type": "motor"}],
        }
    ]
    assert equipment_classes(assets) == ["boiler_feed_pump", "deaerator", "motor"]


def test_cause_candidates_rank_upstream_sources_first() -> None:
    corr = {
        "pairs": [
            {
                "source": {"asset_id": "DEA", "asset_name": "Deaerator", "alarm_name": "Level Low"},
                "target": {"asset_id": "BFP", "asset_name": "Pump", "alarm_name": "Suction Low"},
                "support": 20,
                "confidence": 0.7,
                "lift": 100,
                "avg_lag_minutes": 6,
            },
            {
                "source": {"asset_id": "BFP", "asset_name": "Pump", "alarm_name": "Suction Low"},
                "target": {"asset_id": "BFP", "asset_name": "Pump", "alarm_name": "Discharge Low"},
                "support": 30,
                "confidence": 0.8,
                "lift": 90,
                "avg_lag_minutes": 3,
            },
            {
                "source": {"asset_id": "X", "asset_name": "Other", "alarm_name": "Noise"},
                "target": {"asset_id": "Y", "asset_name": "Unrelated", "alarm_name": "Noise"},
                "support": 50,
                "confidence": 0.9,
                "lift": 5,
                "avg_lag_minutes": 1,
            },
        ]
    }
    cands = cause_candidates(corr, {"BFP"})
    assert [c.source_asset_id for c in cands] == ["DEA", "BFP"]
    assert cands[0].upstream and not cands[1].upstream
    assert cause_candidates(None, {"BFP"}) == []


def test_consistency_conflict_detected() -> None:
    passage = (
        "D1",
        _chunk(
            "SAF-ELE-020",
            "Do not reset the protection relay and do not attempt to restart the "
            "tripped motor until the cause of the trip has been identified.",
        ),
    )
    r = check_recommendation(
        "Reset the motor protection relay and attempt an immediate restart to restore flow", None, [passage]
    )
    assert r.status == "conflict"
    assert r.refs == ["D1"]


def test_consistency_continue_operation_conflict() -> None:
    passage = (
        "D2",
        _chunk(
            "MM-BFP-002",
            "A monitoring-only approach is not acceptable once the bearing "
            "temperature has exceeded the alarm limit for more than 30 minutes; continue "
            "operation only with approval.",
        ),
    )
    r = check_recommendation(
        "Continue operation and monitor bearing temperature for up to 24 hours", None, [passage]
    )
    assert r.status == "conflict"


def test_consistency_supported_and_not_covered() -> None:
    passage = (
        "D3",
        _chunk(
            "SOP-BFP-001",
            "Check deaerator level and pressure. Restore make-up flow and verify the "
            "level control valve is responding.",
            heading="4.2",
        ),
    )
    assert (
        check_recommendation(
            "Check deaerator level and pressure; restore level control", "SOP-BFP-001", [passage]
        ).status
        == "supported"
    )
    assert check_recommendation("Paint the handrails yellow", None, [passage]).status == "not_covered"
    assert check_recommendation("anything", None, []).status == "not_covered"


def test_uncontrolled_documents_never_support_or_conflict() -> None:
    passage = (
        "D4",
        _chunk("KB-VENDOR-099", "Never restart. Check deaerator level and pressure.", trust="uncontrolled"),
    )
    assert check_recommendation("Restart the pump", None, [passage]).status == "not_covered"


def test_procedure_steps_and_action_merge() -> None:
    text = "1. Confirm the low pressure on the local gauge.\n2. Start the standby pump manually now.\nshort\n"
    assert procedure_steps(text) == [
        "Confirm the low pressure on the local gauge.",
        "Start the standby pump manually now.",
    ]
    chunk = _chunk("SOP", text)
    from copilot.evidence import ConsistencyResult

    actions = build_actions(
        ("D1", chunk),
        [{"action": "Do X", "urgency": "soon"}],
        [ConsistencyResult("supported", ["D1"], "ok")],
        "T5",
        [ActionItem(action="Inspect Y", source="investigation")],
    )
    assert [a.source for a in actions] == ["procedure", "procedure", "api", "investigation"]
    assert actions[2].refs == ["T5", "D1"]
