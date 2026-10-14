"""Static master data: plant hierarchy, assets, alarm definitions and causal patterns."""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_TO_PRIORITY = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}


@dataclass(frozen=True)
class Asset:
    asset_id: str
    asset_name: str
    asset_type: str
    site: str
    unit: str
    criticality: str  # A (safety/production critical) | B | C
    manufacturer: str
    model: str
    install_date: str
    tag_prefix: str
    description: str
    operating_limits: dict[str, str] = field(default_factory=dict)
    # (related asset_id, relationship)
    related: tuple[tuple[str, str], ...] = ()
    procedures: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlarmDef:
    alarm_name: str
    alarm_code: str
    alarm_type: str  # process | safety | equipment | device | electrical
    severity: str
    tag_suffix: str
    uom: str
    limit: float
    direction: str  # "high" | "low"
    weekly_rate: float  # background occurrences per week
    chatter: bool = False  # tends to produce short, repeating activations


@dataclass(frozen=True)
class CausalPattern:
    """Episode: ``chain`` fires in order with lags (minutes) between steps."""

    name: str
    chain: tuple[tuple[str, str, int, int], ...]  # (asset_id, alarm_code, min_lag, max_lag)
    weekly_rate: float
    growth: float = 0.0  # relative rate increase across the history (recurring/worsening issue)
    completion: float = 0.85  # probability each subsequent step fires


ALARM_DEFS: dict[str, list[AlarmDef]] = {
    "boiler_feed_pump": [
        AlarmDef(
            "BFP Discharge Pressure Low",
            "BFP_DP_LO",
            "process",
            "critical",
            "PT-201",
            "barg",
            145.0,
            "low",
            0.6,
        ),
        AlarmDef(
            "BFP Suction Pressure Low", "BFP_SP_LO", "process", "high", "PT-200", "barg", 4.5, "low", 0.8
        ),
        AlarmDef(
            "BFP Bearing Temperature High",
            "BFP_BRG_TEMP_HI",
            "equipment",
            "high",
            "TT-210",
            "degC",
            85.0,
            "high",
            0.5,
        ),
        AlarmDef("BFP Vibration High", "BFP_VIB_HI", "equipment", "high", "VT-220", "mm/s", 7.1, "high", 0.4),
        AlarmDef(
            "BFP Minimum Flow Low",
            "BFP_MINFLOW_LO",
            "process",
            "medium",
            "FT-230",
            "t/h",
            60.0,
            "low",
            1.2,
            True,
        ),
        AlarmDef(
            "BFP Seal Leakage High",
            "BFP_SEAL_LEAK_HI",
            "equipment",
            "medium",
            "LT-240",
            "l/h",
            2.0,
            "high",
            0.3,
        ),
    ],
    "motor": [
        AlarmDef("Motor Trip", "MTR_TRIP", "electrical", "critical", "XA-300", "state", 1.0, "high", 0.15),
        AlarmDef(
            "Motor Winding Temperature High",
            "MTR_WDG_TEMP_HI",
            "electrical",
            "high",
            "TT-310",
            "degC",
            130.0,
            "high",
            0.4,
        ),
        AlarmDef(
            "Motor Current High",
            "MTR_CUR_HI",
            "electrical",
            "medium",
            "IT-320",
            "A",
            410.0,
            "high",
            0.9,
            True,
        ),
        AlarmDef(
            "Motor Vibration High", "MTR_VIB_HI", "equipment", "high", "VT-330", "mm/s", 4.5, "high", 0.3
        ),
    ],
    "deaerator": [
        AlarmDef("Deaerator Level Low", "DEA_LVL_LO", "process", "high", "LT-100", "%", 35.0, "low", 0.5),
        AlarmDef(
            "Deaerator Pressure Low", "DEA_PRS_LO", "process", "medium", "PT-110", "barg", 0.8, "low", 0.6
        ),
        AlarmDef("Deaerator Level High", "DEA_LVL_HI", "process", "medium", "LT-101", "%", 85.0, "high", 0.4),
    ],
    "lube_oil_system": [
        AlarmDef(
            "Lube Oil Pressure Low", "LO_PRS_LO", "equipment", "high", "PT-400", "barg", 1.2, "low", 0.4
        ),
        AlarmDef(
            "Lube Oil Filter Differential Pressure High",
            "LO_FLT_DP_HI",
            "device",
            "medium",
            "PDT-410",
            "bar",
            0.8,
            "high",
            0.6,
        ),
        AlarmDef(
            "Lube Oil Temperature High",
            "LO_TEMP_HI",
            "equipment",
            "medium",
            "TT-420",
            "degC",
            60.0,
            "high",
            0.4,
        ),
    ],
    "control_valve": [
        AlarmDef(
            "Valve Position Deviation",
            "VLV_POS_DEV",
            "device",
            "medium",
            "ZT-500",
            "%",
            5.0,
            "high",
            0.8,
            True,
        ),
        AlarmDef(
            "Valve Positioner Fault",
            "VLV_POSITIONER_FLT",
            "device",
            "low",
            "XA-510",
            "state",
            1.0,
            "high",
            0.2,
        ),
    ],
    "compressor": [
        AlarmDef(
            "Compressor Discharge Pressure High",
            "CMP_DP_HI",
            "process",
            "high",
            "PT-601",
            "barg",
            42.0,
            "high",
            1.0,
            True,
        ),
        AlarmDef(
            "Compressor Discharge Temperature High",
            "CMP_DT_HI",
            "process",
            "high",
            "TT-602",
            "degC",
            145.0,
            "high",
            0.5,
        ),
        AlarmDef(
            "Compressor Surge Detected",
            "CMP_SURGE",
            "safety",
            "critical",
            "XA-610",
            "state",
            1.0,
            "high",
            0.12,
        ),
        AlarmDef(
            "Compressor Suction Pressure Low",
            "CMP_SP_LO",
            "process",
            "medium",
            "PT-600",
            "barg",
            8.0,
            "low",
            0.5,
        ),
        AlarmDef(
            "Compressor Vibration High", "CMP_VIB_HI", "equipment", "high", "VT-620", "um", 63.0, "high", 0.3
        ),
    ],
    "heat_exchanger": [
        AlarmDef(
            "Intercooler Outlet Temperature High",
            "HX_OUT_TEMP_HI",
            "process",
            "medium",
            "TT-700",
            "degC",
            45.0,
            "high",
            0.6,
        ),
        AlarmDef(
            "Cooling Water Flow Low",
            "HX_CW_FLOW_LO",
            "process",
            "medium",
            "FT-710",
            "m3/h",
            120.0,
            "low",
            0.4,
        ),
    ],
    "anti_surge_valve": [
        AlarmDef(
            "Anti-Surge Valve Position Deviation",
            "ASV_POS_DEV",
            "device",
            "high",
            "ZT-800",
            "%",
            5.0,
            "high",
            0.4,
        ),
    ],
    "centrifugal_pump": [
        AlarmDef(
            "Pump Discharge Pressure Low", "PMP_DP_LO", "process", "high", "PT-900", "barg", 12.0, "low", 0.4
        ),
        AlarmDef(
            "Pump Vibration High", "PMP_VIB_HI", "equipment", "high", "VT-910", "mm/s", 7.1, "high", 0.4
        ),
        AlarmDef(
            "Pump Seal Pot Level Low",
            "PMP_SEAL_LVL_LO",
            "equipment",
            "medium",
            "LT-920",
            "%",
            30.0,
            "low",
            0.3,
        ),
    ],
    "mcc": [
        AlarmDef(
            "MCC Feeder Earth Fault",
            "MCC_EARTH_FLT",
            "electrical",
            "critical",
            "XA-950",
            "state",
            1.0,
            "high",
            0.05,
        ),
        AlarmDef("MCC Undervoltage", "MCC_UV", "electrical", "high", "ET-960", "V", 5700.0, "low", 0.15),
    ],
    "fired_heater": [
        AlarmDef(
            "Heater Outlet Temperature High",
            "FH_OUT_TEMP_HI",
            "process",
            "high",
            "TT-1000",
            "degC",
            380.0,
            "high",
            0.6,
        ),
        AlarmDef(
            "Heater Flame Failure",
            "FH_FLAME_FAIL",
            "safety",
            "critical",
            "BS-1010",
            "state",
            1.0,
            "high",
            0.05,
        ),
        AlarmDef(
            "Heater Draft Pressure High",
            "FH_DRAFT_HI",
            "process",
            "medium",
            "PT-1020",
            "mbarg",
            -1.0,
            "high",
            0.7,
            True,
        ),
    ],
    "reactor": [
        AlarmDef(
            "Reactor Bed Temperature High",
            "RX_BED_TEMP_HI",
            "process",
            "critical",
            "TT-1100",
            "degC",
            410.0,
            "high",
            0.25,
        ),
        AlarmDef(
            "Reactor Differential Pressure High",
            "RX_DP_HI",
            "process",
            "high",
            "PDT-1110",
            "bar",
            2.5,
            "high",
            0.35,
        ),
    ],
}

_LIMITS_BFP = {
    "discharge_pressure_barg": "150-175",
    "suction_pressure_barg": ">= 5.0",
    "bearing_temp_degC": "<= 85 (trip 95)",
}
_LIMITS_MTR = {"winding_temp_degC": "<= 130 (trip 145)", "current_A": "<= 410", "vibration_mm_s": "<= 4.5"}
_LIMITS_CMP = {
    "discharge_pressure_barg": "36-42",
    "discharge_temp_degC": "<= 145",
    "surge_margin_pct": ">= 10",
}

ASSETS: list[Asset] = [
    # ------------------------------------------------------------- NorthPlant / Unit 1
    Asset(
        "NP-BFP-101",
        "Boiler Feed Pump 101",
        "boiler_feed_pump",
        "NorthPlant",
        "Unit 1",
        "A",
        "Sulzer",
        "HPT 200-4",
        "2014-03-11",
        "U1-BFP101",
        "Multistage centrifugal boiler feed pump, duty pump for HRSG 1 feedwater.",
        _LIMITS_BFP,
        (
            ("NP-MTR-101", "driven_by"),
            ("NP-DEA-001", "suction_source"),
            ("NP-LOS-101", "lubricated_by"),
            ("NP-FCV-101", "discharge_control"),
            ("NP-BFP-103", "standby_pump"),
        ),
        ("SOP-BFP-001", "MM-BFP-002", "TG-BFP-003"),
    ),
    Asset(
        "NP-BFP-103",
        "Boiler Feed Pump 103",
        "boiler_feed_pump",
        "NorthPlant",
        "Unit 1",
        "A",
        "Sulzer",
        "HPT 200-4",
        "2014-03-11",
        "U1-BFP103",
        "Standby boiler feed pump for Unit 1.",
        _LIMITS_BFP,
        (("NP-BFP-101", "duty_pump"), ("NP-DEA-001", "suction_source")),
        ("SOP-BFP-001", "MM-BFP-002"),
    ),
    Asset(
        "NP-MTR-101",
        "Boiler Feed Pump 101 Motor",
        "motor",
        "NorthPlant",
        "Unit 1",
        "A",
        "ABB",
        "AMA 500L4",
        "2014-03-11",
        "U1-M101",
        "6.6 kV induction motor driving Boiler Feed Pump 101.",
        _LIMITS_MTR,
        (("NP-BFP-101", "drives"), ("NP-MCC-001", "fed_from")),
        ("SAF-ELE-020", "MM-MTR-021"),
    ),
    Asset(
        "NP-DEA-001",
        "Deaerator DA-1",
        "deaerator",
        "NorthPlant",
        "Unit 1",
        "A",
        "Stork",
        "DA-1200",
        "2013-11-02",
        "U1-DA1",
        "Spray-tray deaerator supplying suction to BFP 101/103.",
        {"level_pct": "45-75"},
        (("NP-BFP-101", "feeds"), ("NP-BFP-103", "feeds")),
        ("SOP-BFP-001",),
    ),
    Asset(
        "NP-LOS-101",
        "BFP 101 Lube Oil System",
        "lube_oil_system",
        "NorthPlant",
        "Unit 1",
        "A",
        "Sulzer",
        "LOS-200",
        "2014-03-11",
        "U1-LO101",
        "Forced lube oil console for BFP 101 bearings.",
        {"oil_pressure_barg": ">= 1.5"},
        (("NP-BFP-101", "lubricates"),),
        ("MM-BFP-002",),
    ),
    Asset(
        "NP-FCV-101",
        "Feedwater Control Valve FCV-101",
        "control_valve",
        "NorthPlant",
        "Unit 1",
        "B",
        "Fisher",
        "HPT 6in",
        "2014-03-11",
        "U1-FCV101",
        "Feedwater regulating valve downstream of BFP 101.",
        {},
        (("NP-BFP-101", "controls_discharge_of"),),
        (),
    ),
    Asset(
        "NP-MCC-001",
        "MCC-1 6.6kV Switchboard",
        "mcc",
        "NorthPlant",
        "Unit 1",
        "A",
        "Siemens",
        "NXAIR",
        "2013-10-20",
        "U1-MCC1",
        "Medium-voltage switchboard feeding Unit 1 drives.",
        {},
        (("NP-MTR-101", "feeds"),),
        ("SAF-ELE-020",),
    ),
    # ------------------------------------------------------------- NorthPlant / Unit 2
    Asset(
        "NP-BFP-102",
        "Boiler Feed Pump 102",
        "boiler_feed_pump",
        "NorthPlant",
        "Unit 2",
        "A",
        "Sulzer",
        "HPT 200-4",
        "2016-06-01",
        "U2-BFP102",
        "Multistage centrifugal boiler feed pump, duty pump for HRSG 2 feedwater.",
        _LIMITS_BFP,
        (("NP-MTR-102", "driven_by"), ("NP-DEA-002", "suction_source"), ("NP-LOS-102", "lubricated_by")),
        ("SOP-BFP-001", "MM-BFP-002", "TG-BFP-003"),
    ),
    Asset(
        "NP-MTR-102",
        "Boiler Feed Pump 102 Motor",
        "motor",
        "NorthPlant",
        "Unit 2",
        "A",
        "ABB",
        "AMA 500L4",
        "2016-06-01",
        "U2-M102",
        "6.6 kV induction motor driving Boiler Feed Pump 102.",
        _LIMITS_MTR,
        (("NP-BFP-102", "drives"),),
        ("SAF-ELE-020", "MM-MTR-021"),
    ),
    Asset(
        "NP-DEA-002",
        "Deaerator DA-2",
        "deaerator",
        "NorthPlant",
        "Unit 2",
        "A",
        "Stork",
        "DA-1200",
        "2016-05-12",
        "U2-DA2",
        "Spray-tray deaerator supplying suction to BFP 102.",
        {"level_pct": "45-75"},
        (("NP-BFP-102", "feeds"),),
        ("SOP-BFP-001",),
    ),
    Asset(
        "NP-LOS-102",
        "BFP 102 Lube Oil System",
        "lube_oil_system",
        "NorthPlant",
        "Unit 2",
        "A",
        "Sulzer",
        "LOS-200",
        "2016-06-01",
        "U2-LO102",
        "Forced lube oil console for BFP 102 bearings.",
        {"oil_pressure_barg": ">= 1.5"},
        (("NP-BFP-102", "lubricates"),),
        ("MM-BFP-002",),
    ),
    # ------------------------------------------------------------- SouthPlant / Unit 3 (gas compression)
    Asset(
        "SP-CMP-201",
        "Gas Compressor K-201",
        "compressor",
        "SouthPlant",
        "Unit 3",
        "A",
        "Siemens",
        "STC-SV",
        "2018-02-14",
        "U3-K201",
        "Two-stage centrifugal gas compressor, train A.",
        _LIMITS_CMP,
        (("SP-HX-201", "intercooled_by"), ("SP-ASV-201", "protected_by"), ("SP-CMP-202", "parallel_train")),
        ("SOP-CMP-010", "TG-CMP-011"),
    ),
    Asset(
        "SP-CMP-202",
        "Gas Compressor K-202",
        "compressor",
        "SouthPlant",
        "Unit 3",
        "A",
        "Siemens",
        "STC-SV",
        "2018-02-14",
        "U3-K202",
        "Two-stage centrifugal gas compressor, train B.",
        _LIMITS_CMP,
        (("SP-HX-202", "intercooled_by"), ("SP-ASV-202", "protected_by"), ("SP-CMP-201", "parallel_train")),
        ("SOP-CMP-010", "TG-CMP-011"),
    ),
    Asset(
        "SP-CMP-203",
        "Air Compressor K-203",
        "compressor",
        "SouthPlant",
        "Unit 4",
        "B",
        "Atlas Copco",
        "ZR 315",
        "2019-09-30",
        "U4-K203",
        "Instrument air compressor.",
        _LIMITS_CMP,
        (),
        ("SOP-CMP-010",),
    ),
    Asset(
        "SP-HX-201",
        "Intercooler E-201",
        "heat_exchanger",
        "SouthPlant",
        "Unit 3",
        "B",
        "Kelvion",
        "STHE-12",
        "2018-02-14",
        "U3-E201",
        "Interstage cooler for K-201.",
        {"outlet_temp_degC": "<= 45"},
        (("SP-CMP-201", "cools"),),
        ("TG-CMP-011",),
    ),
    Asset(
        "SP-HX-202",
        "Intercooler E-202",
        "heat_exchanger",
        "SouthPlant",
        "Unit 3",
        "B",
        "Kelvion",
        "STHE-12",
        "2018-02-14",
        "U3-E202",
        "Interstage cooler for K-202.",
        {"outlet_temp_degC": "<= 45"},
        (("SP-CMP-202", "cools"),),
        ("TG-CMP-011",),
    ),
    Asset(
        "SP-ASV-201",
        "Anti-Surge Valve FV-201",
        "anti_surge_valve",
        "SouthPlant",
        "Unit 3",
        "A",
        "Mokveld",
        "ASV-8",
        "2018-02-14",
        "U3-FV201",
        "Anti-surge recycle valve for K-201.",
        {},
        (("SP-CMP-201", "protects"),),
        ("SOP-CMP-010",),
    ),
    Asset(
        "SP-ASV-202",
        "Anti-Surge Valve FV-202",
        "anti_surge_valve",
        "SouthPlant",
        "Unit 3",
        "A",
        "Mokveld",
        "ASV-8",
        "2018-02-14",
        "U3-FV202",
        "Anti-surge recycle valve for K-202.",
        {},
        (("SP-CMP-202", "protects"),),
        ("SOP-CMP-010",),
    ),
    Asset(
        "SP-PMP-401",
        "Cooling Water Pump 401",
        "centrifugal_pump",
        "SouthPlant",
        "Unit 4",
        "B",
        "KSB",
        "Omega 300",
        "2017-04-03",
        "U4-P401",
        "Cooling water circulation pump supplying E-201/E-202.",
        {},
        (("SP-HX-201", "supplies"), ("SP-HX-202", "supplies")),
        (),
    ),
    # ------------------------------------------------------------- EastRefinery / Unit 5
    Asset(
        "ER-MTR-501",
        "Crude Charge Pump Motor M-501",
        "motor",
        "EastRefinery",
        "Unit 5",
        "A",
        "WEG",
        "HGF 450",
        "2015-08-21",
        "U5-M501",
        "Motor driving crude charge pump P-501.",
        _LIMITS_MTR,
        (("ER-PMP-501", "drives"), ("ER-MCC-005", "fed_from")),
        ("SAF-ELE-020", "MM-MTR-021"),
    ),
    Asset(
        "ER-PMP-501",
        "Crude Charge Pump P-501",
        "centrifugal_pump",
        "EastRefinery",
        "Unit 5",
        "A",
        "Flowserve",
        "HDX 8x10",
        "2015-08-21",
        "U5-P501",
        "Crude charge pump feeding heater F-601.",
        {},
        (("ER-MTR-501", "driven_by"), ("ER-FH-601", "feeds")),
        (),
    ),
    Asset(
        "ER-MTR-502",
        "Recycle Gas Compressor Motor M-502",
        "motor",
        "EastRefinery",
        "Unit 5",
        "A",
        "WEG",
        "HGF 500",
        "2015-08-21",
        "U5-M502",
        "Motor driving recycle gas compressor.",
        _LIMITS_MTR,
        (("ER-MCC-005", "fed_from"),),
        ("SAF-ELE-020", "MM-MTR-021"),
    ),
    Asset(
        "ER-MTR-503",
        "Cooling Tower Fan Motor M-503",
        "motor",
        "EastRefinery",
        "Unit 5",
        "C",
        "WEG",
        "W22",
        "2016-01-10",
        "U5-M503",
        "Cooling tower fan motor.",
        _LIMITS_MTR,
        (("ER-MCC-005", "fed_from"),),
        ("MM-MTR-021",),
    ),
    Asset(
        "ER-MCC-005",
        "MCC-5 6.6kV Switchboard",
        "mcc",
        "EastRefinery",
        "Unit 5",
        "A",
        "Schneider",
        "MCset",
        "2015-07-01",
        "U5-MCC5",
        "Medium-voltage switchboard for Unit 5 drives.",
        {},
        (("ER-MTR-501", "feeds"), ("ER-MTR-502", "feeds"), ("ER-MTR-503", "feeds")),
        ("SAF-ELE-020",),
    ),
    # ------------------------------------------------------------- EastRefinery / Unit 6
    Asset(
        "ER-FH-601",
        "Charge Heater F-601",
        "fired_heater",
        "EastRefinery",
        "Unit 6",
        "A",
        "Heurtey",
        "Cabin",
        "2012-05-05",
        "U6-F601",
        "Hydrotreater charge heater.",
        {"outlet_temp_degC": "<= 380"},
        (("ER-RX-601", "feeds"), ("ER-PMP-501", "fed_by")),
        ("SAF-GEN-040",),
    ),
    Asset(
        "ER-RX-601",
        "Hydrotreater Reactor R-601",
        "reactor",
        "EastRefinery",
        "Unit 6",
        "A",
        "JGC",
        "HDT-R",
        "2012-05-05",
        "U6-R601",
        "Diesel hydrotreater reactor.",
        {"bed_temp_degC": "<= 410"},
        (("ER-FH-601", "fed_by"),),
        ("SAF-GEN-040",),
    ),
]

ASSET_INDEX: dict[str, Asset] = {a.asset_id: a for a in ASSETS}

CAUSAL_PATTERNS: list[CausalPattern] = [
    # BFP 101 cavitation chain: deaerator level dips -> suction pressure low -> discharge pressure low (worsening)
    CausalPattern(
        "bfp101_cavitation",
        (
            ("NP-DEA-001", "DEA_LVL_LO", 0, 0),
            ("NP-BFP-101", "BFP_SP_LO", 2, 8),
            ("NP-BFP-101", "BFP_DP_LO", 1, 4),
            ("NP-BFP-101", "BFP_VIB_HI", 1, 6),
        ),
        weekly_rate=0.9,
        growth=3.0,
        completion=0.8,
    ),
    # BFP 101 lubrication chain: filter dP -> oil pressure low -> bearing temperature high
    CausalPattern(
        "bfp101_lubrication",
        (
            ("NP-LOS-101", "LO_FLT_DP_HI", 0, 0),
            ("NP-LOS-101", "LO_PRS_LO", 5, 30),
            ("NP-BFP-101", "BFP_BRG_TEMP_HI", 3, 12),
        ),
        weekly_rate=0.5,
        growth=2.0,
        completion=0.85,
    ),
    # BFP 102 lubrication chain
    CausalPattern(
        "bfp102_lubrication",
        (
            ("NP-LOS-102", "LO_PRS_LO", 0, 0),
            ("NP-BFP-102", "BFP_BRG_TEMP_HI", 3, 12),
            ("NP-BFP-102", "BFP_VIB_HI", 2, 10),
        ),
        weekly_rate=0.6,
        growth=0.8,
    ),
    # Compressor discharge pressure: intercooler fouling + anti-surge valve deviation
    CausalPattern(
        "k201_discharge_pressure",
        (
            ("SP-HX-201", "HX_OUT_TEMP_HI", 0, 0),
            ("SP-CMP-201", "CMP_DT_HI", 2, 10),
            ("SP-CMP-201", "CMP_DP_HI", 1, 6),
        ),
        weekly_rate=1.5,
        growth=1.0,
    ),
    CausalPattern(
        "k202_antisurge",
        (
            ("SP-ASV-202", "ASV_POS_DEV", 0, 0),
            ("SP-CMP-202", "CMP_DP_HI", 1, 5),
            ("SP-CMP-202", "CMP_SURGE", 1, 4),
        ),
        weekly_rate=0.8,
        growth=0.6,
        completion=0.7,
    ),
    CausalPattern(
        "cw_pump_to_intercooler",
        (
            ("SP-PMP-401", "PMP_DP_LO", 0, 0),
            ("SP-HX-201", "HX_CW_FLOW_LO", 1, 5),
            ("SP-HX-201", "HX_OUT_TEMP_HI", 3, 12),
            ("SP-CMP-201", "CMP_DP_HI", 2, 8),
        ),
        weekly_rate=0.4,
    ),
    # Motor trip chain in EastRefinery unit 5
    CausalPattern(
        "m501_trip",
        (
            ("ER-MTR-501", "MTR_CUR_HI", 0, 0),
            ("ER-MTR-501", "MTR_WDG_TEMP_HI", 5, 20),
            ("ER-MTR-501", "MTR_TRIP", 2, 10),
            ("ER-PMP-501", "PMP_DP_LO", 0, 2),
        ),
        weekly_rate=0.35,
        growth=0.8,
        completion=0.75,
    ),
    CausalPattern(
        "mcc5_undervoltage",
        (("ER-MCC-005", "MCC_UV", 0, 0), ("ER-MTR-502", "MTR_TRIP", 0, 2), ("ER-MTR-503", "MTR_TRIP", 0, 2)),
        weekly_rate=0.12,
        completion=0.6,
    ),
    # BFP 101 motor trip (rare) after winding temperature
    CausalPattern(
        "m101_trip",
        (
            ("NP-MTR-101", "MTR_WDG_TEMP_HI", 0, 0),
            ("NP-MTR-101", "MTR_TRIP", 5, 25),
            ("NP-BFP-101", "BFP_DP_LO", 0, 2),
        ),
        weekly_rate=0.1,
        completion=0.6,
    ),
    CausalPattern(
        "f601_heater",
        (
            ("ER-PMP-501", "PMP_DP_LO", 0, 0),
            ("ER-FH-601", "FH_OUT_TEMP_HI", 3, 15),
            ("ER-RX-601", "RX_BED_TEMP_HI", 10, 30),
        ),
        weekly_rate=0.3,
    ),
]

# Unit-wide alarm floods (e.g. unit upsets) used by flood analysis.
FLOOD_UNITS: dict[str, float] = {"Unit 2": 0.35, "Unit 3": 0.25, "Unit 5": 0.15}  # floods per week


def alarm_def(asset_type: str, alarm_code: str) -> AlarmDef:
    for d in ALARM_DEFS[asset_type]:
        if d.alarm_code == alarm_code:
            return d
    raise KeyError(f"{asset_type}/{alarm_code}")
