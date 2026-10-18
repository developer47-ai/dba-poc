---
doc_id: SOP-ESC-001
title: Create an incident ticket
version: "1.0"
status: active
kind: procedure
owner: NetCo Customer Service
effective_from: "2026-10-01"
agent_scope: [orchestrator, inhome, access-pon, access-p2p, access-copper]
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL, EOF, GOF]
symptoms: [escalation, unresolved problem, incident ticket]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Hand an unresolved problem to the right NetCo team with everything they need, so the customer
does not have to explain it again.

## When to use

A procedure ends with "escalate to SOP-ESC-001", or the expert decides the problem cannot be
solved during the call.

## Procedure

### S1 — Summarise the case
- actor: agent
- next: { ready: S2 }
Write a short summary: the customer's complaint, every check done with its result, every action
taken (reboot, reset) and the current status.

### S2 — Create the incident
- actor: agent
- tool: ticketing.create_incident
- requires_consent: true
- next: { created: S3, declined: S4 }
Show the summary to the expert and create the incident after approval.

### S3 — Inform the customer
- actor: expert
- end: resolved
Give the incident number and the expected response time (one working day).

### S4 — No incident
- actor: expert
- end: escalate
- escalate_to: team lead
Document why no incident was created and inform the team lead.
