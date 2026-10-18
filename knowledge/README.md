# NetCo knowledge library

Source of truth for the agents' knowledge search (RAG) and their troubleshooting procedures.
All content is **fictional** and written for this demo.

| Folder | Kind | Used for |
|---|---|---|
| [`sop/`](sop/) | `procedure` | Step-by-step troubleshooting flows the agents follow and cite |
| [`reference/`](reference/) | `reference` | Facts to look up (indicator lights, Wi-Fi basics) |

## Writing a document

Every file is Markdown with a YAML header:

```markdown
---
doc_id: SOP-IH-001            # SOP-<AREA>-nnn for procedures, KB-<AREA>-nnn for reference
title: No internet while the modem is online
version: "1.0"
status: active                # active | draft | retired (only active documents are searched)
kind: procedure               # procedure | reference
owner: In-Home Support
agent_scope: [inhome]         # orchestrator | inhome | access-pon | access-p2p | access-copper
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL]
symptoms: [no internet, internet light orange]
user_groups: [customer_service]
entry_step: S1                # procedures only
---

## Purpose
...

## Procedure

### S1 — Check the internet session
- actor: agent                                  # agent | expert | customer
- tool: network-diagnostics.get_ppp_session     # optional; only actor 'agent' calls tools
- next: { up: S7, down: S2 }                     # outcome -> next step
Instruction text: what to do and how to decide the outcome.

### S5 — Problem solved
- actor: expert
- end: resolved                                  # resolved | escalate | handover | continue
Instruction text.
```

Step fields must directly follow the step heading, one `- key: value` per line.
End steps add a target: `escalate_to` (team or SOP id), `handover_to` (agent) or
`continue_with` (SOP id).

## Rules the validator enforces

- every `next` target exists, every step is reachable from `entry_step` and can reach an end;
- tool names exist in `sop_kit.catalog`;
- **a step that changes the customer's service (reboot, reset, booking, ticket, order) must set
  `requires_consent: true`**, so the agent always asks the support expert first;
- references to other SOPs point to documents in this library.

Check the whole library with `uv run python -m sop_kit knowledge`.
