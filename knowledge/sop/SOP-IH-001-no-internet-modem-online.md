---
doc_id: SOP-IH-001
title: No internet while the modem is online
version: "1.0"
status: active
kind: procedure
owner: NetCo In-Home Support
effective_from: "2026-10-01"
agent_scope: [inhome]
carrier_types: [fiber, copper]
carrier_tech_types: [GPON, XGSPON, VDSL, EOF, GOF]
device_models: [NetCo Hub 2, NetCo Hub 3, NetCo Hub 4]
symptoms: [no internet, internet light orange, PPP session down, modem online but no connection]
user_groups: [customer_service]
entry_step: S1
---

## Purpose

Restore internet for a customer whose modem is powered and online but who has no internet
connection. The usual cause is a modem that cannot (re-)establish its PPP session; a remote
reboot fixes most cases.

## When to use

The customer reports "no internet" and the case is in the home: the access network was checked
or the customer has no indication of an outage. Do not use for slow internet or a single device
that cannot connect while others can (use SOP-IH-002).

## Procedure

### S1 — Check the internet session
- actor: agent
- tool: network-diagnostics.get_ppp_session
- next: { up: S7, down: S2 }
Read the PPP session status. Outcome `up` if the status is up, `down` otherwise.

### S2 — Check the indicator lights
- actor: agent
- tool: network-diagnostics.get_led_status
- next: { access_problem: S9, modem_fault: S6, modem_stuck: S3 }
Use the light colours (see KB-LED-001) to decide:
`access_problem` if the ONT `los` light is red or the modem `wan` light is red;
`modem_fault` if the modem `power` light is orange;
`modem_stuck` if the `wan` light is green but `internet` is orange or off.

### S3 — Reboot the modem remotely
- actor: agent
- tool: network-diagnostics.reboot_device
- requires_consent: true
- next: { done: S4, declined: S10 }
Explain to the expert that the reboot interrupts the customer's internet and phone for about a
minute, and ask for approval. Reboot the `modem` only after approval (`done`); if the expert or
customer does not agree, the outcome is `declined`.

### S4 — Check the internet session again
- actor: agent
- tool: network-diagnostics.get_ppp_session
- next: { up: S5, down: S6 }
Give the modem about a minute to start, then read the PPP session again.

### S5 — Confirm with the customer
- actor: expert
- end: resolved
Ask the customer to open a website. Internet works again: the problem is solved.
Advise the customer to restart the modem themselves if it happens again.

### S6 — Suspect a modem fault
- actor: agent
- end: continue
- continue_with: SOP-IH-003
The modem stays without internet although the line is fine: continue with modem replacement.

### S7 — Internet session is up
- actor: agent
- end: continue
- continue_with: SOP-IH-002
The connection to the internet works. If devices still cannot get online, the cause is the
Wi-Fi or the devices: continue with SOP-IH-002.

### S9 — Problem outside the home
- actor: agent
- end: handover
- handover_to: access-network
The lights show the fiber or line connection is down. Hand over to the access network agent for
the customer's technology.

### S10 — Reboot declined
- actor: expert
- end: escalate
- escalate_to: SOP-ESC-001
Explain that without a reboot the cause cannot be fixed remotely and offer an incident ticket.
