---
doc_id: SOP-CU-001
title: Copper (VDSL) line check
version: "1.0"
status: active
kind: procedure
owner: NetCo Copper Access Operations
effective_from: "2026-10-01"
agent_scope: [access-copper]
carrier_types: [copper]
carrier_tech_types: [VDSL]
symptoms: [no internet, DSL light off, line not in sync, PPP session down]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Check whether a copper (VDSL) customer's line is in sync. A line in sync means the problem is in
the home; a line out of sync is a network or wiring fault.

## When to use

The customer has a copper connection and no internet.

## Procedure

### S1 — Check the DSLAM port and sync
- actor: agent
- tool: network-diagnostics.get_access_port_status
- next: { in_sync: S4, port_down: S2 }
Outcome `in_sync` if the port status is up with sync rates; `port_down` otherwise.
A healthy VDSL line has a noise margin (`snr_margin_db`) of 6 dB or more.

### S2 — Check the DSL cable and splitter
- actor: customer
- next: { fixed: S3, not_fixed: S5 }
Ask the customer to connect the modem's DSL port directly to the main wall socket with the
supplied cable, without extension cords or old splitters.

### S3 — Check sync again
- actor: agent
- tool: network-diagnostics.get_access_port_status
- next: { in_sync: S4, port_down: S5 }
Read the port status again.

### S4 — Line in sync: problem is in the home
- actor: agent
- end: handover
- handover_to: inhome
The copper line works. Hand over to the In-Home agent (modem or Wi-Fi).

### S5 — Create an incident
- actor: agent
- tool: ticketing.create_incident
- requires_consent: true
- next: { created: S6, declined: S7 }
The line stays out of sync: create an incident for the copper access team after approval.

### S6 — Inform the customer
- actor: expert
- end: resolved
Give the incident number; a line fault is usually repaired within two working days.

### S7 — Customer declined
- actor: expert
- end: escalate
- escalate_to: team lead
Document the checks and inform the team lead.
