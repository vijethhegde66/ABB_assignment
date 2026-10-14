---
doc_id: TG-BFP-003
title: Boiler Feed Pump Troubleshooting Guide
doc_type: troubleshooting_guide
equipment_classes: [boiler_feed_pump, deaerator, lube_oil_system, control_valve]
alarm_codes: [BFP_DP_LO, BFP_SP_LO, BFP_BRG_TEMP_HI, BFP_VIB_HI, DEA_LVL_LO, LO_PRS_LO]
sites: [NorthPlant]
revision: "3"
effective_date: 2025-11-20
owner: Reliability Engineering
trust_level: controlled
---

# Boiler Feed Pump Troubleshooting Guide (TG-BFP-003)

## 1. How to Use This Guide

Start from the alarm or symptom, check the listed causes in order of likelihood, and confirm each cause with the diagnostic evidence before taking corrective action. Always follow the alarm response in SOP-BFP-001 first; this guide supports the follow-up investigation.

## 2. Recurring Low Suction or Discharge Pressure

Likely causes, in order of likelihood:

1. Deaerator level control instability. A sluggish or sticking level control valve (LCV-100), positioner air leaks or poorly tuned level control cause level dips during load changes. The deaerator level low alarm then precedes the BFP suction pressure low alarm.
2. Clogged suction strainer, raising suction losses (see MM-BFP-002 Section 4).
3. Deaerator pressure decay during load reduction (pegging steam control), reducing available NPSH.
4. Passing minimum-flow recirculation valve, reducing net discharge pressure.

Diagnostic evidence: if Deaerator Level Low alarms precede BFP Suction Pressure Low alarms by 2 - 10 minutes on a recurring basis, the root cause is upstream inventory control, not the pump itself. Low discharge pressure and high vibration following the suction alarm within minutes confirm cavitation. Repeated cavitation damages impellers and mechanical seals; schedule a vibration survey and seal inspection.

## 3. High Bearing Temperature

Likely causes:

1. Lube oil filter blockage leading to low oil pressure (filter differential pressure high -> lube oil pressure low -> bearing temperature high, typically within 5 - 30 minutes).
2. Lube oil cooler fouling or cooling water problems (oil temperature high).
3. Oil degradation or water contamination.
4. Misalignment or bearing wear (check vibration 2x component).

## 4. High Vibration

Causes: cavitation (with low suction pressure), misalignment, unbalance after impeller repair, bearing wear, loose foundation bolts. Use spectrum analysis per MM-BFP-002 Section 6.

## 5. Investigation Workflow for Recurring High-Severity Alarms

When high or critical alarms recur on a boiler feed pump over the review period (typically 90 days):

1. Summarize alarms by alarm name and compute the recurring rate and acknowledgement delay.
2. Correlate the pump alarms with alarms on related assets: deaerator, level control valve, lube oil console, pump motor and standby pump.
3. Identify leading indicators (alarms that consistently occur first) - these point to the contributing factor.
4. Inspect the related assets indicated by the correlation: deaerator level control loop (LCV-100 stroke test, positioner), suction strainer, lube oil filter and oil quality, motor protection history.
5. Review open work orders on the pump and related assets; raise a work request for unresolved causes.
6. Confirm that the alarm setpoints and priorities are correct (ALM-PHIL-030) and that chattering alarms are rationalized.
