---
doc_id: SOP-P2P-001
title: Point-to-point fiber link down
version: "1.0"
status: active
kind: procedure
owner: NetCo Fiber Access Operations
effective_from: "2026-10-01"
agent_scope: [access-p2p]
carrier_types: [fiber]
carrier_tech_types: [EOF, GOF]
symptoms: [no internet, WAN light red, access port down, link down]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Restore internet for a point-to-point fiber (EoF/GoF) customer, whose modem connects directly to
a port on a NetCo aggregation switch without an ONT.

## When to use

The customer's technology is EOF or GOF and the modem's WAN light is red, or the In-Home agent
handed over because the problem is outside the home.

## Procedure

### S1 — Check the access port
- actor: agent
- tool: network-diagnostics.get_access_port_status
- next: { port_down: S2, port_up: S5 }
Outcome `port_down` if the switch port status is down, `port_up` otherwise.

### S2 — Check the fiber cable to the modem
- actor: customer
- next: { fixed: S3, not_fixed: S4 }
Ask the customer to check the fiber cable from the wall outlet to the modem's WAN port: clicked
in at both ends and not damaged.

### S3 — Check the access port again
- actor: agent
- tool: network-diagnostics.get_access_port_status
- next: { port_up: S6, port_down: S4 }
Read the port status again.

### S4 — Create an incident for the access team
- actor: agent
- tool: ticketing.create_incident
- requires_consent: true
- next: { created: S6, declined: S7 }
The port stays down: the fault is in NetCo's network. Summarise the checks and create an incident
after the expert approves.

### S5 — Access port is up
- actor: agent
- end: handover
- handover_to: inhome
The link to the modem works; the cause is in the home. Hand over to the In-Home agent.

### S6 — Inform the customer
- actor: expert
- end: resolved
Tell the customer the link is restored, or give the incident number and that the access team
usually repairs port faults within one working day.

### S7 — Customer declined
- actor: expert
- end: escalate
- escalate_to: team lead
Document the checks and inform the team lead.
