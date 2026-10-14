from datetime import UTC, datetime, timedelta

import pytest

from alarm_simulator.store import AlarmStore, InvalidRequest, NotFound, Scope

START = datetime(2026, 4, 1, tzinfo=UTC)
END = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def store() -> AlarmStore:
    return AlarmStore(42, START, END)


def test_generation_is_deterministic(store: AlarmStore) -> None:
    other = AlarmStore(42, START, END)
    assert len(store.alarms) == len(other.alarms) > 1000
    assert [a.alarm_id for a in store.alarms[:50]] == [a.alarm_id for a in other.alarms[:50]]
    assert store.alarms[100].to_dict(END) == other.alarms[100].to_dict(END)
    assert len(AlarmStore(7, START, END).alarms) != len(store.alarms)


def test_past_history_stable_when_end_moves(store: AlarmStore) -> None:
    longer = AlarmStore(42, START, END + timedelta(days=5))
    early = [a for a in store.alarms if a.start_time < END - timedelta(days=1)][:200]
    assert [a.to_dict(END)["start_time"] for a in early] == [
        a.to_dict(END)["start_time"] for a in longer.alarms[:200]
    ]


def test_search_assets_exact_and_fuzzy(store: AlarmStore) -> None:
    assert store.search_assets("Boiler Feed Pump 101")[0]["asset_id"] == "NP-BFP-101"
    assert store.search_assets("Boiler Feed Pump 999") == []
    names = {r["asset_type"] for r in store.search_assets("compressor")}
    assert "compressor" in names
    assert all(r["unit"] == "Unit 5" for r in store.search_assets("motor", unit="Unit 5"))


def test_filters_and_pagination(store: AlarmStore) -> None:
    res = store.list_alarms(
        Scope(asset_ids=["NP-BFP-101"], severities=["critical"]), 1, 10, "start_time", "desc"
    )
    assert res["pagination"]["page_size"] == 10 and res["pagination"]["has_next"]
    assert all(a["severity"] == "critical" and a["asset_id"] == "NP-BFP-101" for a in res["data"])
    times = [a["start_time"] for a in res["data"]]
    assert times == sorted(times, reverse=True)
    with pytest.raises(InvalidRequest):
        store.list_alarms(Scope(), 1, 10, "drop table alarms", "desc")


def test_active_alarms_present_at_end(store: AlarmStore) -> None:
    active = store.filter(Scope(site="EastRefinery", status="active"))
    assert any(a.alarm_code == "MTR_TRIP" and a.severity == "critical" for a in active)
    bfp102 = store.filter(Scope(asset_ids=["NP-BFP-102"], status="active", severities=["critical"]))
    assert bfp102 and bfp102[0].alarm_code == "BFP_DP_LO"


def test_summary_kpis(store: AlarmStore) -> None:
    s = store.summary(
        Scope(
            asset_ids=["NP-BFP-101"], start=END - timedelta(days=90), end=END, severities=["high", "critical"]
        ),
        ["alarm_name"],
        ["alarm_count", "recurring_rate", "avg_ack_delay", "critical_count"],
    )
    assert s["total_alarms"] == sum(g["alarm_count"] for g in s["groups"])
    assert 0 <= s["kpis"]["recurring_rate"] <= 1 and s["kpis"]["avg_ack_delay_seconds"] > 0
    assert s["groups"][0]["alarm_count"] >= s["groups"][-1]["alarm_count"]


def test_correlation_recovers_embedded_causal_pattern(store: AlarmStore) -> None:
    res = store.correlation(
        ["NP-BFP-101"], END - timedelta(days=90), END, 15, "medium", 2, True, "cooccurrence"
    )
    top = {(p["source"]["alarm_code"], p["target"]["alarm_code"]) for p in res["pairs"][:8]}
    assert ("DEA_LVL_LO", "BFP_SP_LO") in top
    assert res["leading_indicators"][0]["asset_id"] == "NP-DEA-001"
    with pytest.raises(NotFound):
        store.correlation(["NOPE-1"], START, END, 15, "medium", 1, True, "cooccurrence")


def test_trend_detects_worsening(store: AlarmStore) -> None:
    t = store.trends(
        Scope(
            asset_ids=["NP-BFP-101"], start=END - timedelta(days=90), end=END, severities=["high", "critical"]
        ),
        "weekly",
        ["alarm_count"],
    )
    assert t["trend"]["second_half_count"] > t["trend"]["first_half_count"]
    with pytest.raises(InvalidRequest):
        store.trends(Scope(start=START, end=END + timedelta(days=300)), "hourly", ["alarm_count"])


def test_priority_score_and_bands(store: AlarmStore) -> None:
    trip = next(
        a for a in store.filter(Scope(site="EastRefinery", status="active")) if a.alarm_code == "MTR_TRIP"
    )
    p = store.priority_score(trip.alarm_id)
    assert p["priority_band"] == "urgent" and p["priority_score"] >= 80
    assert sum(f["points"] for f in p["factors"]) >= p["priority_score"]


def test_floods_rationalization_and_calculations(store: AlarmStore) -> None:
    f = store.flood_analysis(Scope(unit="Unit 2", start=START, end=END), 10, 10)
    assert f["flood_count"] > 0 and all(w["alarm_count"] >= 10 for w in f["flood_windows"])
    r = store.rationalization_candidates(Scope(site="NorthPlant", start=START, end=END), 6, 180)
    assert any("chattering" in c["reasons"] for c in r["candidates"])
    calc = store.generate_calculation("critical_alarm_density", {"unit": "Unit 3"})
    out = store.execute_calculation(calc["calculation_id"], {"start_time": "2026-05-01T00:00:00Z"})
    assert out["result"]["unit"] == "critical alarms per day"
    with pytest.raises(InvalidRequest):
        store.generate_calculation("rm -rf", {})
    with pytest.raises(NotFound):
        store.execute_calculation("CALC-000000000000", None)
