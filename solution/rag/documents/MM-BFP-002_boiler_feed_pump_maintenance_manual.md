---
doc_id: MM-BFP-002
title: Boiler Feed Pump Maintenance Manual
doc_type: maintenance_manual
equipment_classes: [boiler_feed_pump, lube_oil_system]
alarm_codes: [BFP_BRG_TEMP_HI, BFP_VIB_HI, BFP_SEAL_LEAK_HI, BFP_SP_LO, LO_PRS_LO, LO_FLT_DP_HI, LO_TEMP_HI]
sites: [NorthPlant]
revision: "4"
effective_date: 2025-09-01
owner: Maintenance Manager, NorthPlant
trust_level: controlled
---

# Boiler Feed Pump Maintenance Manual (MM-BFP-002)

## 1. Scope

Covers preventive and corrective maintenance of the Sulzer HPT 200-4 multistage boiler feed pumps and their forced lube oil consoles (LOS-200).

## 2. Lubrication System

2.1 The forced lube oil system shall maintain a bearing header pressure of at least 1.5 barg. The auxiliary oil pump starts automatically at 1.2 barg (Lube Oil Pressure Low alarm).

2.2 The duplex lube oil filter shall be changed over when the differential pressure exceeds 0.8 bar (Lube Oil Filter Differential Pressure High alarm). Change-over procedure: open the filling valve to prime the standby element, wait for vent to run clear, rotate the change-over valve slowly, then replace the dirty element. Never run with both elements isolated.

2.3 A rising filter differential pressure followed by low oil pressure and high bearing temperature is the typical signature of oil contamination. Take an oil sample and check water content (limit 200 ppm) and particle count (ISO 4406 target 17/15/12).

2.4 Oil analysis interval: every 3 months, and after any bearing temperature excursion above 90 °C.

## 3. Bearings

3.1 Journal and thrust bearings are white-metal (babbitt) lined. Normal metal temperature is below 75 °C; the alarm is set at 85 °C and the trip at 95 °C.

3.2 Continuous operation with bearing temperature above 85 °C accelerates babbitt degradation. A monitoring-only approach is not acceptable once the temperature has exceeded the alarm limit for more than 30 minutes or is approaching 95 °C: the pump shall be changed over to the standby pump and the bearing inspected.

3.3 Bearing inspection: check for wiping, scoring and discoloration; measure clearances (journal clearance 0.15 - 0.22 mm). Replace the bearing if clearance exceeds 0.25 mm.

## 4. Suction Strainer

4.1 The temporary/permanent suction strainer shall be cleaned when the differential pressure exceeds 0.5 bar.

4.2 After recurring BFP Suction Pressure Low alarms, inspect the strainer for debris (deaerator internals, scale, gasket fragments) even if the differential pressure transmitter reads normal; transmitter impulse lines can be blocked.

## 5. Mechanical Seal

Seal leakage above 2 l/h requires inspection of the seal faces and the seal flush cooler. Repeated cavitation episodes damage seal faces; record cavitation history in the work order.

## 6. Vibration Monitoring

6.1 Overall velocity limits (ISO 10816-7, category I pumps): good below 3.5 mm/s, alert 7.1 mm/s, trip 11 mm/s.

6.2 Spectrum analysis: broadband high-frequency noise indicates cavitation; 1x running speed indicates unbalance; 2x indicates misalignment; non-synchronous peaks indicate rolling element or oil whirl problems.

6.3 A vibration survey shall be performed after any recurring cavitation (suction pressure low) episode.

## 7. Coupling Alignment

Laser alignment tolerances: offset 0.05 mm, angularity 0.05 mm/100 mm. Re-check alignment after any motor or pump removal and after foundation work.

## 8. Preventive Maintenance Schedule

| Task | Interval |
|---|---|
| Oil sample analysis | 3 months |
| Filter element change | On dP > 0.8 bar or 12 months |
| Vibration route | Monthly |
| Suction strainer inspection | 12 months or after recurring suction alarms |
| Bearing inspection | 24,000 running hours |
| Alignment check | 12 months |
