---
doc_id: KB-LED-001
title: Indicator lights of NetCo modems and FibreLink ONTs
version: "1.0"
status: active
kind: reference
owner: NetCo In-Home Support
effective_from: "2026-10-01"
agent_scope: [inhome, access-pon, access-p2p, access-copper]
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL, EOF, GOF]
device_models: [NetCo Hub 2, NetCo Hub 3, NetCo Hub 4, FibreLink ONT-100, FibreLink ONT-200]
symptoms: [light colours, LED meaning, modem lights, ONT lights]
user_groups: [customer_service]
---

## NetCo Hub modem lights

| Light | Colour | Meaning | What to do |
|---|---|---|---|
| power | green | Modem is on and healthy | – |
| power | orange | Hardware fault detected | Modem replacement (SOP-IH-003) |
| power | off | No power | Check adapter and wall socket |
| wan | green | Connection to the ONT, DSL line or fiber port is up | – |
| wan | red | No connection on the WAN side | Problem outside the home: access network |
| internet | green | Internet session (PPP) is up | – |
| internet | orange | Line is up but no internet session | Usually a stuck modem: reboot (SOP-IH-001) |
| internet | off | No internet session and no line | Check the wan light first |
| wifi | green | Wi-Fi is on with the active settings | – |
| wifi | orange | Wi-Fi is on but settings were changed or devices fail to connect | Compare with the sticker (SOP-IH-002) |
| wifi | off | Wi-Fi is switched off | Switch on or reset Wi-Fi |

## FibreLink ONT lights (GPON / XGSPON)

| Light | Colour | Meaning | What to do |
|---|---|---|---|
| power | green | ONT has power | – |
| power | off | No power | SOP-PON-002 |
| pon | green | Registered on the fiber network | – |
| pon | off | Not registered on the network | Check the los light |
| los | off | Optical signal received (normal) | – |
| los | red | Loss of signal: no light from the network | SOP-PON-001 |

Normal received optical power (`rx_power_dbm`) is between -8 and -27 dBm.

## Point-to-point and copper

Point-to-point (EoF/GoF) and copper (VDSL) connections have no ONT; the modem's `wan` light shows
the state of the line. Use the access port status to see whether the network side is up.
