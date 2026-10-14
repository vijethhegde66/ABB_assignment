---
doc_id: TG-CMP-011
title: Compressor Troubleshooting - Repeated Discharge Pressure Alarms
doc_type: troubleshooting_guide
equipment_classes: [compressor, heat_exchanger, anti_surge_valve, centrifugal_pump]
alarm_codes: [CMP_DP_HI, CMP_DT_HI, HX_OUT_TEMP_HI, HX_CW_FLOW_LO, ASV_POS_DEV, CMP_SURGE]
sites: [SouthPlant]
revision: "2"
effective_date: 2025-12-05
owner: Reliability Engineering
trust_level: controlled
---

# Compressor Troubleshooting - Repeated Discharge Pressure Alarms (TG-CMP-011)

## 1. Why Discharge Pressure Alarms Repeat

Repeated compressor discharge pressure high alarms usually have one of the following causes:

1. Intercooler fouling: the intercooler outlet temperature rises above 45 °C, increasing second-stage inlet temperature, discharge temperature and discharge pressure. Signature: Intercooler Outlet Temperature High precedes Compressor Discharge Temperature High and Discharge Pressure High by 2 - 15 minutes.
2. Cooling water supply problems: low cooling water flow (cooling water pump discharge pressure low) degrades intercooling.
3. Anti-surge valve calibration drift or positioner faults: Anti-Surge Valve Position Deviation alarms followed by discharge pressure excursions and, in severe cases, surge.
4. Downstream restriction: partially closed downstream valves, fouled check valves or high downstream header pressure.
5. Alarm design: an alarm setpoint too close to the normal operating point without deadband or on-delay causes chattering (many alarms of short duration without a real excursion).

## 2. Diagnostic Steps

1. Correlate discharge pressure alarms with intercooler outlet temperature, cooling water flow and anti-surge valve deviation alarms over the last 30 - 90 days.
2. If intercooler temperature alarms lead, schedule tube bundle cleaning and check cooling water flow.
3. If anti-surge valve deviation alarms lead, perform a valve stroke test and positioner calibration.
4. If most activations last less than 60 seconds, review the alarm setpoint, deadband (1 - 2 % of span) and on-delay (3 - 5 s) per ALM-PHIL-030.

## 3. Related Assets to Inspect

Intercoolers E-201 / E-202, cooling water pump P-401, anti-surge valves FV-201 / FV-202, downstream header valves and the pressure control valve.
