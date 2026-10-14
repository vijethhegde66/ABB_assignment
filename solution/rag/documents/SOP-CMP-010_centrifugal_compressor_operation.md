---
doc_id: SOP-CMP-010
title: Centrifugal Gas Compressor Operation and Alarm Response
doc_type: operating_procedure
equipment_classes: [compressor, anti_surge_valve, heat_exchanger]
alarm_codes: [CMP_DP_HI, CMP_SURGE, CMP_DT_HI, CMP_SP_LO, CMP_VIB_HI, ASV_POS_DEV]
sites: [SouthPlant]
revision: "5"
effective_date: 2026-02-01
owner: Operations Superintendent, SouthPlant
trust_level: controlled
---

# Centrifugal Gas Compressor Operation and Alarm Response (SOP-CMP-010)

## 1. Scope

Applies to gas compressors K-201 and K-202 (Unit 3) and air compressor K-203 (Unit 4) at SouthPlant.

## 2. Operating Limits

Discharge pressure 36 - 42 barg (high alarm 42 barg, trip 46 barg); discharge temperature below 145 °C; surge margin at least 10 %; intercooler outlet temperature below 45 °C.

## 3. Alarm Response

### 3.1 Compressor Discharge Pressure High (CMP_DP_HI)

1. Check downstream block valves and the discharge pressure control valve position; a closed or throttled downstream valve is the most common cause of a sudden high discharge pressure.
2. Check the intercooler outlet temperature and cooling water flow; poor intercooling raises second-stage discharge pressure and temperature.
3. Verify that the anti-surge valve responds and is not deviating from its demanded position.
4. If pressure continues to rise towards 46 barg, reduce compressor load (inlet guide vanes / speed) and inform the shift supervisor.
5. Repeated short activations (chattering) of this alarm without process change shall be reported for setpoint and deadband review under ALM-PHIL-030.

### 3.2 Compressor Surge Detected (CMP_SURGE)

1. Confirm that the anti-surge valve has opened. Never place the anti-surge controller in manual or block the anti-surge valve during operation.
2. Reduce compressor load and increase recycle.
3. If surge repeats twice within 10 minutes, trip the compressor and investigate before restart.

### 3.3 Compressor Discharge Temperature High (CMP_DT_HI)

Check intercooler and aftercooler performance and cooling water supply. Reduce load if temperature exceeds 150 °C.

## 4. Safety

High-pressure gas: hot work and line breaking require a permit to work (SAF-GEN-040). Hydrocarbon gas detection must be in service before any field intervention on the compressor skid.
