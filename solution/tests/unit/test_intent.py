import pytest

from copilot.intent import extract_asset_phrases, understand


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        (
            "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.",
            "active_response",
        ),
        ("Why are compressor discharge pressure alarms repeatedly occurring?", "investigate"),
        ("Which alarm has the highest priority in EastRefinery, and why?", "priority_ranking"),
        ("What related assets should be inspected for this motor trip alarm?", "related_assets"),
        ("Which operating procedure applies to this alarm?", "procedure_lookup"),
        ("Are the API recommendations consistent with the maintenance manual?", "consistency_check"),
        (
            "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify "
            "likely contributing factors, retrieve the relevant operating procedure, and provide recommended "
            "actions with source evidence.",
            "investigate",
        ),
        ("Show alarm floods and chattering alarms in Unit 2 last month", "alarm_performance"),
        ("What is the PPE required in process areas?", "document_question"),
    ],
)
def test_intent_classification(question: str, intent: str) -> None:
    assert understand(question).intent == intent


def test_entities_acceptance_scenario() -> None:
    u = understand(
        "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days"
    )
    assert u.asset_phrases == ["Boiler Feed Pump 101"]
    assert u.severities == ["high", "critical"]
    assert u.lookback_days == 90
    assert not u.generic_asset


def test_generic_equipment_class_and_alarm_terms() -> None:
    u = understand("Why are compressor discharge pressure alarms repeatedly occurring?")
    assert u.asset_phrases == ["compressor"]
    assert u.generic_asset
    assert "discharge pressure" in u.alarm_terms


def test_context_reference_without_asset() -> None:
    u = understand("What related assets should be inspected for this motor trip alarm?")
    assert u.refers_to_context
    assert u.asset_phrases == []
    assert "motor trip" in u.alarm_terms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("trip on Crude Charge Pump Motor M-501 again", ["Crude Charge Pump Motor M-501"]),
        ("check K-201 vibration", ["K-201"]),
        ("Gas Compressor K-202 surge", ["Gas Compressor K-202"]),
    ],
)
def test_specific_asset_phrases(text: str, expected: list[str]) -> None:
    phrases, generic = extract_asset_phrases(text)
    assert phrases == expected
    assert not generic


def test_identifiers_and_scope() -> None:
    u = understand("Score ALM-0001234 on NP-BFP-101 in Unit 2 at NorthPlant for the past 2 weeks")
    assert u.alarm_id == "ALM-0001234"
    assert u.asset_ids == ["NP-BFP-101"]
    assert u.unit == "Unit 2"
    assert u.site == "NorthPlant"
    assert u.lookback_days == 14


def test_status_and_time_words() -> None:
    u = understand("current alarms on Boiler Feed Pump 101 this week")
    assert u.status == "active"
    assert u.lookback_days == 7
