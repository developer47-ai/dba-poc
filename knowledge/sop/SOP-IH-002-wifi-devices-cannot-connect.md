---
doc_id: SOP-IH-002
title: Devices cannot connect to Wi-Fi
version: "1.0"
status: active
kind: procedure
owner: NetCo In-Home Support
effective_from: "2026-10-01"
agent_scope: [inhome]
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL, EOF, GOF]
device_models: [NetCo Hub 2, NetCo Hub 3, NetCo Hub 4]
symptoms: [wifi not working, devices cannot connect, wifi network not visible, wrong wifi password]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Get the customer's devices back on Wi-Fi when the internet connection itself works. Customers
often change the Wi-Fi name, password or security mode and then cannot connect any more;
resetting Wi-Fi to the values on the modem sticker fixes this.

## When to use

The PPP session is up (SOP-IH-001 step S7) but the customer's devices cannot get online over
Wi-Fi, or the customer cannot find their Wi-Fi network.

## Procedure

### S1 — Compare the Wi-Fi settings with the sticker
- actor: agent
- tool: network-diagnostics.get_wifi_settings
- next: { matches_defaults: S4, differs_from_sticker: S2 }
Outcome `matches_defaults` if `matches_factory_defaults` is true, otherwise
`differs_from_sticker` (for example a changed name, a hidden network or WPA3 that older devices
do not support).

### S2 — Reset Wi-Fi to the sticker values
- actor: agent
- tool: network-diagnostics.reset_wifi
- requires_consent: true
- next: { done: S3, declined: S8 }
Explain that the Wi-Fi name and password go back to the values printed on the modem sticker and
that every device must reconnect with the sticker password. Reset only after approval.

### S3 — Check that devices reconnect
- actor: agent
- tool: network-diagnostics.get_managed_devices
- next: { connected: S5, still_disconnected: S7 }
After about a minute, check whether the customer's devices are connected again.

### S4 — Check the connected devices
- actor: agent
- tool: network-diagnostics.get_managed_devices
- next: { connected: S5, disconnected: S7 }
The settings are the factory values. See whether devices are connected.

### S5 — Confirm with the customer
- actor: expert
- end: resolved
Ask the customer to try a website on the device that had problems. The problem is solved.

### S7 — Reconnect the device
- actor: customer
- next: { fixed: S5, not_fixed: S8 }
Ask the customer to "forget" the Wi-Fi network on the device, select the network name from the
sticker and enter the sticker password. Outcome `fixed` if the device connects.

### S8 — Escalate
- actor: expert
- end: escalate
- escalate_to: SOP-ESC-001
The devices still cannot connect, or the customer declined the reset. Create an incident.
