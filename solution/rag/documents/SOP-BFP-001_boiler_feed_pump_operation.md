---
doc_id: SOP-BFP-001
title: Boiler Feed Pump Operation and Alarm Response Procedure
doc_type: operating_procedure
equipment_classes: [boiler_feed_pump, deaerator, motor]
alarm_codes: [BFP_DP_LO, BFP_SP_LO, BFP_BRG_TEMP_HI, BFP_VIB_HI, BFP_MINFLOW_LO, MTR_TRIP, DEA_LVL_LO]
sites: [NorthPlant]
revision: "7"
effective_date: 2026-01-15
owner: Operations Superintendent, NorthPlant
trust_level: controlled
---

# Boiler Feed Pump Operation and Alarm Response Procedure (SOP-BFP-001)

## 1. Purpose and Scope

This procedure defines normal operation, alarm response and changeover of the boiler feed pumps (BFP 101, BFP 102, BFP 103) at NorthPlant Units 1 and 2. It applies to control room operators, field operators and shift supervisors. Boiler feed pumps are criticality A assets: loss of feedwater can cause a boiler/HRSG trip and damage to drum internals.

## 2. Safety Precautions

2.1 Never bypass or defeat a pump trip, low suction pressure trip or minimum-flow protection. Bypassing a protection requires a bypass permit approved by the shift supervisor in line with SAF-GEN-040.

2.2 Boiler feed pumps operate at discharge pressures above 150 barg and temperatures above 150 °C. Field checks require hearing protection, face shield near flanges and heat-resistant gloves.

2.3 Electrical faults on the pump motor are handled under SAF-ELE-020 Electrical Safety and Motor Trip Response.

## 3. Normal Operating Limits

| Parameter | Normal range | Alarm | Trip |
|---|---|---|---|
| Suction pressure | >= 5.0 barg | 4.5 barg (low) | 3.5 barg |
| Discharge pressure | 150 - 175 barg | 145 barg (low) | - |
| Bearing temperature | <= 75 °C | 85 °C (high) | 95 °C |
| Vibration (overall velocity) | <= 4.5 mm/s | 7.1 mm/s | 11 mm/s |
| Deaerator level | 45 - 75 % | 35 % (low) | 20 % |

## 4. Alarm Response

### 4.1 BFP Discharge Pressure Low (BFP_DP_LO)

Immediate actions (critical priority, respond within 5 minutes):

1. Confirm the low discharge pressure on the local gauge and on the DCS trend to rule out a transmitter fault.
2. Verify that the standby boiler feed pump has auto-started. If it has not and discharge pressure is below 145 barg and falling, start the standby pump manually following Section 6.
3. Check deaerator level and BFP suction pressure. Low discharge pressure that follows low suction pressure indicates cavitation caused by insufficient suction head (see TG-BFP-003 Section 2).
4. Check that the minimum-flow recirculation valve is not stuck open or passing.
5. If both duty and standby pumps cannot maintain pressure, reduce boiler load and inform the shift supervisor.

### 4.2 BFP Suction Pressure Low (BFP_SP_LO)

1. Check deaerator level (normal 45 - 75 %) and deaerator pressure. Restore make-up / condensate flow and verify the level control valve LCV-100 is responding.
2. Check the suction strainer differential pressure. A differential pressure above 0.5 bar indicates a clogged strainer: change over to the standby pump and request strainer cleaning (MM-BFP-002 Section 4).
3. If suction pressure falls below 4.0 barg with cavitation noise or rising vibration, start the standby pump and stop the affected pump before the 3.5 barg trip.

### 4.3 BFP Bearing Temperature High (BFP_BRG_TEMP_HI)

1. Check lube oil pressure (minimum 1.5 barg), lube oil temperature and lube oil filter differential pressure on the BFP lube oil console.
2. If oil pressure is low, start the auxiliary lube oil pump and change over the duplex filter (MM-BFP-002 Section 2).
3. If bearing temperature exceeds 95 °C, or is rising faster than 2 °C per minute, start the standby pump and stop the affected pump.
4. Do not continue extended operation above the 85 °C alarm limit. If the temperature stays above the alarm limit for more than 30 minutes, the pump shall be changed over and a work request raised. Continued operation requires written approval from the reliability engineer.

### 4.4 BFP Vibration High (BFP_VIB_HI)

1. Compare the vibration trend with suction pressure and bearing temperature. Vibration that rises together with low suction pressure indicates cavitation; vibration with rising bearing temperature indicates a lubrication or bearing problem.
2. If vibration exceeds 11 mm/s the pump trips. Above 7.1 mm/s change over to the standby pump and request a vibration spectrum analysis.

### 4.5 Pump Motor Trip (MTR_TRIP)

1. Confirm that the standby pump auto-started and feedwater flow is restored.
2. Do not reset the motor protection relay or attempt to restart the tripped motor. Follow SAF-ELE-020: record the protection relay flags and call the electrical technician.
3. Inspect related equipment before any restart: the pump (free rotation, seal leakage), the coupling, the lube oil system and the MCC feeder.

### 4.6 BFP Minimum Flow Low (BFP_MINFLOW_LO)

Verify that the minimum-flow recirculation valve opens. Repeated short activations of this alarm (chattering) shall be reported for rationalization under ALM-PHIL-030.

## 5. Recurring Alarms

If any BFP alarm recurs more than 3 times in 7 days, or a high/critical alarm recurs on the same pump in consecutive shifts, the shift supervisor shall raise a work request and notify the reliability engineer, who shall perform an investigation following TG-BFP-003 Section 5. Recurring low suction pressure alarms shall always include an inspection of the deaerator level control loop and the suction strainer.

## 6. Changeover to the Standby Pump

1. Confirm the standby pump is available (no open isolation permit, lube oil pressure normal, suction valve open, warm-up valve open for at least 30 minutes).
2. Start the standby pump from the DCS and confirm discharge pressure rises above 150 barg.
3. Open the standby pump discharge valve; confirm feedwater flow is stable.
4. Stop the duty pump; close its discharge valve if it is to be isolated for maintenance.
5. Record the changeover and reason in the shift log.

## 7. References

SAF-GEN-040 General Process Safety Instructions; SAF-ELE-020 Electrical Safety and Motor Trip Response; MM-BFP-002 Boiler Feed Pump Maintenance Manual; TG-BFP-003 Boiler Feed Pump Troubleshooting Guide; ALM-PHIL-030 Alarm Management Philosophy.
