"""Rule-based operator recommendation engine of the (simulated) Alarm Management system.

The rules intentionally mirror a real vendor rule base: most actions agree
with the plant's controlled procedures, but a few are generic and *conflict*
with site documents (e.g. "attempt immediate restart" after a motor trip).
Detecting such conflicts is part of the copilot's job.
"""

from __future__ import annotations

import itertools
from collections import Counter
from datetime import timedelta
from typing import Any

from alarm_simulator.catalog import ASSET_INDEX
from alarm_simulator.store import AlarmStore, Scope

# alarm_code -> list of (action, rationale, urgency, reference_procedure)
RULES: dict[str, list[tuple[str, str, str, str | None]]] = {
    "BFP_DP_LO": [
        (
            "Confirm discharge pressure on local gauge and DCS trend",
            "Rule out transmitter fault",
            "immediate",
            "SOP-BFP-001",
        ),
        (
            "Start the standby boiler feed pump if discharge pressure keeps falling",
            "Maintain drum/HRSG feedwater supply",
            "immediate",
            "SOP-BFP-001",
        ),
        (
            "Check deaerator level and BFP suction pressure for cavitation",
            "Low NPSH is the most common cause",
            "immediate",
            "TG-BFP-003",
        ),
        (
            "Verify recirculation (minimum flow) valve is not stuck open",
            "Recirculation bypass reduces discharge pressure",
            "soon",
            "TG-BFP-003",
        ),
    ],
    "BFP_SP_LO": [
        (
            "Check deaerator level and pressure; restore level control",
            "Suction pressure follows deaerator level",
            "immediate",
            "SOP-BFP-001",
        ),
        (
            "Inspect suction strainer differential pressure",
            "Clogged strainer restricts suction",
            "soon",
            "MM-BFP-002",
        ),
        (
            "Listen for cavitation noise and check vibration trend",
            "Cavitation damages impellers",
            "soon",
            "TG-BFP-003",
        ),
    ],
    "BFP_BRG_TEMP_HI": [
        (
            "Check lube oil pressure, temperature and filter differential pressure",
            "Bearing temperature follows lubrication",
            "immediate",
            "MM-BFP-002",
        ),
        (
            "Continue operation and monitor bearing temperature for up to 24 hours",
            "Generic vendor guidance for slow temperature drift",
            "routine",
            None,
        ),
        ("Take an oil sample for analysis", "Detect contamination / water ingress", "soon", "MM-BFP-002"),
    ],
    "BFP_VIB_HI": [
        (
            "Compare vibration with suction pressure and bearing temperature trends",
            "Distinguish cavitation vs. mechanical",
            "immediate",
            "TG-BFP-003",
        ),
        (
            "Request vibration spectrum analysis",
            "Identify misalignment, unbalance or bearing defects",
            "soon",
            "MM-BFP-002",
        ),
    ],
    "BFP_MINFLOW_LO": [
        (
            "Verify minimum-flow recirculation valve opens",
            "Protects pump from overheating at low flow",
            "immediate",
            "SOP-BFP-001",
        ),
    ],
    "BFP_SEAL_LEAK_HI": [
        ("Inspect mechanical seal and seal flush line", "Seal wear causes leakage", "soon", "MM-BFP-002"),
    ],
    "MTR_TRIP": [
        (
            "Reset the motor protection relay and attempt an immediate restart to restore flow",
            "Generic rule: minimise downtime",
            "immediate",
            None,
        ),
        (
            "Start the standby equipment if available",
            "Maintain process continuity",
            "immediate",
            "SOP-BFP-001",
        ),
        (
            "Record protection relay flags (overcurrent, earth fault, thermal)",
            "Identify trip cause",
            "immediate",
            "SAF-ELE-020",
        ),
    ],
    "MTR_WDG_TEMP_HI": [
        (
            "Check motor load current and cooling (fan, air filters)",
            "Overload or poor cooling raises winding temperature",
            "immediate",
            "MM-MTR-021",
        ),
        ("Reduce load on driven equipment if possible", "Reduce I2R heating", "soon", "MM-MTR-021"),
    ],
    "MTR_CUR_HI": [
        (
            "Check driven equipment for mechanical binding or process overload",
            "High current indicates overload",
            "soon",
            "MM-MTR-021",
        ),
    ],
    "MTR_VIB_HI": [
        ("Check coupling alignment and foundation bolts", "Common mechanical causes", "soon", "MM-MTR-021"),
    ],
    "DEA_LVL_LO": [
        (
            "Check make-up water / condensate supply and level control valve",
            "Restore deaerator inventory",
            "immediate",
            "SOP-BFP-001",
        ),
    ],
    "LO_PRS_LO": [
        (
            "Start auxiliary lube oil pump and check oil reservoir level",
            "Protect bearings",
            "immediate",
            "MM-BFP-002",
        ),
        (
            "Check lube oil filter differential pressure and switch filters if required",
            "Clogged filter restricts flow",
            "immediate",
            "MM-BFP-002",
        ),
    ],
    "LO_FLT_DP_HI": [
        (
            "Switch to the standby lube oil filter and replace the dirty element",
            "Prevent low oil pressure",
            "soon",
            "MM-BFP-002",
        ),
    ],
    "CMP_DP_HI": [
        (
            "Check downstream block valves and pressure control valve position",
            "Downstream restriction raises discharge pressure",
            "immediate",
            "SOP-CMP-010",
        ),
        (
            "Check intercooler outlet temperature and cooling water flow",
            "Poor intercooling raises discharge pressure/temperature",
            "soon",
            "TG-CMP-011",
        ),
        (
            "Verify anti-surge valve response and calibration",
            "Anti-surge valve deviation linked to pressure excursions",
            "soon",
            "TG-CMP-011",
        ),
        (
            "Review alarm setpoint and deadband for chattering",
            "Repeated short alarms indicate deadband issue",
            "routine",
            "ALM-PHIL-030",
        ),
    ],
    "CMP_SURGE": [
        (
            "Confirm anti-surge valve opened; reduce compressor load",
            "Surge damages seals and bearings",
            "immediate",
            "SOP-CMP-010",
        ),
    ],
    "CMP_DT_HI": [
        (
            "Check intercooler performance and cooling water supply",
            "High discharge temperature follows poor cooling",
            "soon",
            "TG-CMP-011",
        ),
    ],
    "PMP_DP_LO": [
        (
            "Check pump suction conditions and driver status",
            "Low discharge pressure may follow driver trip",
            "immediate",
            None,
        ),
    ],
    "FH_OUT_TEMP_HI": [
        (
            "Reduce firing rate and verify charge flow",
            "Low charge flow raises outlet temperature",
            "immediate",
            "SAF-GEN-040",
        ),
    ],
    "RX_BED_TEMP_HI": [
        (
            "Reduce heater outlet temperature and increase quench",
            "Prevent temperature runaway",
            "immediate",
            "SAF-GEN-040",
        ),
    ],
}

GENERIC = [
    (
        "Acknowledge the alarm, verify the measurement and consult the applicable procedure",
        "No specific rule configured for this alarm",
        "soon",
        "ALM-PHIL-030",
    )
]


def operator_actions(
    store: AlarmStore,
    alarm_id: str,
    include_related: bool,
    include_asset_context: bool,
    include_historical_pattern: bool,
) -> dict[str, Any]:
    alarm = store.alarm(alarm_id)
    asset = ASSET_INDEX[alarm.asset_id]
    rules = RULES.get(alarm.alarm_code, GENERIC)
    recs = [
        {
            "step": i + 1,
            "action": action,
            "rationale": why,
            "urgency": urgency,
            "reference_procedure": ref,
            "source": "alarm-management-rule-engine",
        }
        for i, (action, why, urgency, ref) in enumerate(rules)
    ]
    out: dict[str, Any] = {
        "alarm_id": alarm.alarm_id,
        "alarm_name": alarm.alarm_name,
        "alarm_code": alarm.alarm_code,
        "severity": alarm.severity,
        "asset_id": alarm.asset_id,
        "asset_name": alarm.asset_name,
        "recommendations": recs,
        "confidence": 0.8 if alarm.alarm_code in RULES else 0.4,
    }
    if include_related:
        window_start = alarm.start_time - timedelta(hours=2)
        related_ids = [rid for rid, _ in asset.related]
        nearby = store.filter(
            Scope(asset_ids=related_ids, start=window_start, end=alarm.start_time + timedelta(hours=1))
        )
        active_near: Counter[str] = Counter(a.asset_id for a in nearby)
        out["related_assets_to_check"] = [
            {
                "asset_id": rid,
                "asset_name": ASSET_INDEX[rid].asset_name,
                "relationship": rel,
                "alarms_within_2h": active_near.get(rid, 0),
                "reason": (
                    f"{active_near[rid]} alarm(s) on this asset within 2h of the event"
                    if active_near.get(rid)
                    else f"Linked asset ({rel.replace('_', ' ')})"
                ),
            }
            for rid, rel in asset.related
        ]
        out["related_assets_to_check"].sort(key=lambda r: -r["alarms_within_2h"])
    if include_asset_context:
        out["asset_context"] = {
            "asset_type": asset.asset_type,
            "criticality": asset.criticality,
            "site": asset.site,
            "unit": asset.unit,
            "operating_limits": asset.operating_limits,
        }
    if include_historical_pattern:
        hist = store.filter(
            Scope(
                asset_ids=[alarm.asset_id], start=alarm.start_time - timedelta(days=90), end=alarm.start_time
            )
        )
        same = [a for a in hist if a.alarm_code == alarm.alarm_code]
        gaps = [(b.start_time - a.start_time).total_seconds() / 3600 for a, b in itertools.pairwise(same)]
        out["historical_pattern"] = {
            "occurrences_90d": len(same),
            "mean_hours_between": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "last_occurrence": same[-1].start_time.isoformat().replace("+00:00", "Z") if same else None,
            "co_alarms_on_asset": [
                {"alarm_name": k, "count": v}
                for k, v in Counter(
                    a.alarm_name for a in hist if a.alarm_code != alarm.alarm_code
                ).most_common(3)
            ],
        }
    return out
