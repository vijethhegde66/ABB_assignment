---
doc_id: SAF-ELE-020
title: Electrical Safety and Motor Trip Response
doc_type: safety_instruction
equipment_classes: [motor, mcc, boiler_feed_pump, centrifugal_pump]
alarm_codes: [MTR_TRIP, MTR_WDG_TEMP_HI, MCC_EARTH_FLT, MCC_UV]
sites: [NorthPlant, SouthPlant, EastRefinery]
revision: "9"
effective_date: 2026-03-01
owner: Electrical Authority / HSE
trust_level: controlled
---

# Electrical Safety and Motor Trip Response (SAF-ELE-020)

## 1. Scope

Applies to all low- and medium-voltage motors and motor control centres (MCC) at all sites.

## 2. Motor Trip Response

2.1 When a motor trips, start the standby equipment if available to maintain the process. Starting a healthy standby unit is permitted.

2.2 Do not reset the protection relay and do not attempt to restart the tripped motor until the cause of the trip has been identified by authorised electrical personnel. Repeated restart attempts on a faulted motor can cause winding burn-out, fire or arc flash.

2.3 Record all protection relay flags (overcurrent, thermal overload, earth fault, differential, undervoltage) before any reset.

2.4 If earth fault or differential protection has operated, the motor shall not be restarted. Isolate the motor, apply lock-out/tag-out (Section 4) and perform insulation resistance testing.

2.5 If thermal overload has operated, allow the motor to cool for at least 30 minutes. A maximum of one restart attempt is allowed after the driven equipment has been inspected and the shift supervisor has approved the restart.

## 3. Related Equipment to Inspect After a Motor Trip

1. Driven equipment: check the pump or compressor turns freely, no seizure, seal leakage or blocked discharge.
2. Coupling and alignment.
3. Motor cooling: fan, air filters, ambient temperature.
4. MCC feeder / switchgear: relay flags, contactor condition, cable terminations (thermographic survey).
5. Upstream supply: undervoltage events on the MCC bus can trip several motors simultaneously; check whether other motors on the same MCC tripped at the same time.

## 4. Lock-Out / Tag-Out

Isolation of motors and MCC feeders requires an electrical isolation certificate. Apply personal locks, verify zero energy by test-before-touch, and earth medium-voltage feeders before work.

## 5. Arc Flash

Operating medium-voltage switchgear with doors open requires arc-flash PPE category 4. Remote racking shall be used where installed.
