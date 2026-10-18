---
doc_id: SOP-IH-003
title: Modem replacement (swap order)
version: "1.0"
status: active
kind: procedure
owner: NetCo In-Home Support
effective_from: "2026-10-01"
agent_scope: [inhome]
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL, EOF, GOF]
device_models: [NetCo Hub 2, NetCo Hub 3, NetCo Hub 4]
symptoms: [modem defective, power light orange, modem keeps failing, no internet after reboot]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Replace a defective modem. The customer receives a new modem by post within two working days and
returns the old one in the same box.

## When to use

The line is fine but the modem does not restore the internet session, even after a reboot, or
its power light is orange (hardware fault).

## Procedure

### S1 — Record the modem details
- actor: agent
- tool: network-diagnostics.get_device_details
- next: { recorded: S2 }
Note the modem model, serial number and firmware for the order.

### S2 — Factory reset as a last remote attempt
- actor: agent
- tool: network-diagnostics.factory_reset_device
- requires_consent: true
- next: { done: S3, declined: S4 }
A factory reset erases all the customer's own settings (Wi-Fi name, port forwarding). Ask for
approval; skip it if the modem's power light is orange (outcome `declined`).

### S3 — Check the internet session
- actor: agent
- tool: network-diagnostics.get_ppp_session
- next: { up: S6, down: S4 }
Wait about two minutes after the reset, then check the PPP session.

### S4 — Create the swap order
- actor: agent
- tool: ticketing.create_swap_order
- requires_consent: true
- next: { created: S5 }
Create a swap order for the recorded modem. Confirm the delivery address with the expert first.

### S5 — Inform the customer
- actor: expert
- end: resolved
Give the order number. The new modem arrives within two working days; the customer connects it
the same way and returns the old modem with the enclosed label.

### S6 — Solved by the reset
- actor: expert
- end: resolved
Internet works after the factory reset. Help the customer reconnect devices with the sticker
password.
