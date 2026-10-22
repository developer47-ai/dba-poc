# access-pon-agent

Specialist for fiber (GPON / XGS-PON) problems outside the home. It uses the same SOP engine as the
In-Home agent ([`libs/sop-agent`](../../../libs/sop-agent)); only its identity differs
(`access-pon`, port 8202), so it follows the procedures scoped to `access-pon`.

## What it demonstrates

A case arrives from the In-Home agent through the orchestrator ("handed over from … findings so
far …"). For customer `C1001` (GPON, OLT port down):

1. **SOP-PON-001 S1** – reads the ONT/OLT status (`network-diagnostics-mcp`).
2. **S5** – searches appointment slots (`scheduling-mcp`, read-only).
3. **S6** – proposes the earliest slot and **stops for the expert's approval**.
4. Approved → `book_appointment` runs and the SOP ends resolved; declined → nothing is booked and
   the SOP escalates.

If the optical signal is fine, SOP-PON-001 hands the case back to the In-Home agent.

## Run the tests

```bash
uv run pytest services/agents/access-pon
```

The tests use real MCP servers and the network simulator; only the LLM is scripted.
