---
doc_id: KB-WIFI-001
title: Wi-Fi basics for NetCo modems
version: "1.0"
status: active
kind: reference
owner: NetCo In-Home Support
effective_from: "2026-10-01"
agent_scope: [inhome]
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL, EOF, GOF]
device_models: [NetCo Hub 2, NetCo Hub 3, NetCo Hub 4]
symptoms: [wifi slow, wifi range, wifi channel, wifi password, hidden network, WPA3]
user_groups: [customer_service]
---

## Factory settings on the sticker

Every NetCo Hub has a sticker with the factory Wi-Fi name (`NetCo-xxxx`), password and security
mode (WPA2). A Wi-Fi reset restores exactly these values; NetCo never asks customers for their
own password.

## Security modes

WPA2 works with practically all devices. WPA3 is more secure but some older printers, TVs and
smart speakers cannot connect to a WPA3-only network. A **hidden network** does not appear in the
device's list of networks; the name must be typed in by hand.

## Bands and channels

| Band | Range | Speed | Typical use |
|---|---|---|---|
| 2.4 GHz | Longer, through walls | Lower | Smart home devices, far rooms |
| 5 GHz | Shorter | Higher | Laptops, TVs, phones near the modem |

On 2.4 GHz only channels 1, 6 and 11 do not overlap; channel 13 is not supported by some devices
sold outside Europe.

## Signal strength

`rssi_dbm` of -30 to -60 is good, -60 to -70 is fair, weaker than -70 is poor. For poor signal,
move the device closer or advise a NetCo Wi-Fi extender.
