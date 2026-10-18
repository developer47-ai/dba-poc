---
doc_id: SOP-PON-001
title: No optical signal at the ONT (LOS light red)
version: "1.0"
status: active
kind: procedure
owner: NetCo Fiber Access Operations
effective_from: "2026-10-01"
agent_scope: [access-pon]
carrier_types: [fiber]
carrier_tech_types: [GPON, XGSPON]
device_models: [FibreLink ONT-100, FibreLink ONT-200]
symptoms: [no internet, LOS light red, no optical signal, PON light off, fiber down]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Find out why a GPON/XGSPON fiber customer's ONT receives no light, fix what the customer can fix
(a loose fiber patch cable) and otherwise book an engineer.

## When to use

The ONT's LOS light is red or its PON light is off, or the In-Home agent handed over because the
problem is outside the home.

## Procedure

### S1 — Check the ONT and the OLT port
- actor: agent
- tool: network-diagnostics.get_ont_status
- next: { olt_port_down: S5, no_signal_port_up: S2, signal_ok: S7 }
Outcome `olt_port_down` if `olt_port_status` is down (the fault is in the network);
`no_signal_port_up` if the port is up but `optical_signal` is false; `signal_ok` if the ONT
receives light.

### S2 — Check the fiber patch cable
- actor: customer
- next: { fixed: S3, not_fixed: S5 }
Ask the customer to check the thin fiber cable between the wall outlet (FTU) and the ONT: both
ends clicked in, not bent sharply, not damaged. Never look into the fiber end.

### S3 — Check the optical signal again
- actor: agent
- tool: network-diagnostics.get_ont_status
- next: { signal_ok: S8, no_signal: S5 }
Read the ONT status again after the cable check.

### S5 — Find an engineer appointment
- actor: agent
- tool: scheduling.search_appointment_slots
- next: { slots_found: S6, none_available: S9 }
The fault needs an engineer. Search appointment slots for the next working days.

### S6 — Book the engineer
- actor: agent
- tool: scheduling.book_appointment
- requires_consent: true
- next: { booked: S8, declined: S9 }
Propose the earliest slots to the expert, who agrees one with the customer. Book only the slot
the expert approves.

### S7 — Optical signal is fine
- actor: agent
- end: handover
- handover_to: inhome
The fiber connection works up to the ONT; the cause is in the home. Hand over to the In-Home agent.

### S8 — Done
- actor: expert
- end: resolved
Confirm the outcome to the customer (signal restored, or the appointment date and time).

### S9 — Escalate
- actor: expert
- end: escalate
- escalate_to: SOP-ESC-001
No suitable appointment or the customer declined: create an incident for the access team.
