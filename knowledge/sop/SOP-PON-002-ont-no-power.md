---
doc_id: SOP-PON-002
title: ONT has no power
version: "1.0"
status: active
kind: procedure
owner: NetCo Fiber Access Operations
effective_from: "2026-10-01"
agent_scope: [access-pon]
carrier_types: [fiber]
carrier_tech_types: [GPON, XGSPON]
device_models: [FibreLink ONT-100, FibreLink ONT-200]
symptoms: [ONT lights off, ONT power light off, ONT dead]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Restore power to a fiber ONT whose lights are all off.

## When to use

The ONT shows no lights at all. If only the LOS light is red, use SOP-PON-001.

## Procedure

### S1 — Check the power supply
- actor: customer
- next: { power_restored: S2, still_off: S4 }
Ask the customer to check that the ONT's power adapter is plugged into a working wall socket
(test the socket with a lamp) and firmly into the ONT.

### S2 — Check the optical signal
- actor: agent
- tool: network-diagnostics.get_ont_status
- next: { signal_ok: S3, no_signal: S5 }
Give the ONT two minutes to start, then read its status.

### S3 — Solved
- actor: expert
- end: resolved
The ONT has power and signal again.

### S4 — ONT replacement needed
- actor: expert
- end: escalate
- escalate_to: SOP-ESC-001
The ONT stays off with a working socket: create an incident; the access team replaces the ONT.

### S5 — Power is back but no signal
- actor: agent
- end: continue
- continue_with: SOP-PON-001
Continue with the no-optical-signal procedure.
