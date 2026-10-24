# orchestrator-agent

Entry point for the support expert's conversation. It keeps sessions, decides which specialist
agent handles each turn, and relays the agent's events to the chat UI **in real time** over
Server-Sent Events (SSE).

## API

```http
POST /v1/sessions                       { "customer_id": "C1002", "expert_name": "Sam" }   -> 201
GET  /v1/sessions?customer_id=C1001      the customer's earlier cases, newest first
GET  /v1/sessions/{session_id}          session summary: history, pending approval, consent audit log
POST /v1/sessions/{session_id}/messages { "message": "Customer has no internet" }        -> text/event-stream
POST /v1/sessions/{session_id}/consent  { "consent_id": "…", "approved": true, "comment": "…" } -> text/event-stream
GET  /health
```

## Human-in-the-loop approval

When an SOP step would change the customer's service (reboot, Wi-Fi reset, factory reset), the
agent emits a `consent_request` and **does not act**. The expert answers with `POST …/consent`:

- only the **pending** request can be decided (`409` otherwise, also on a second click or while a
  turn runs); the decision carries no arguments, so it cannot change *what* runs: the agent
  executes the action and arguments it saved when it asked, after checking them again;
- every decision is added to `consent_log` (who, when, action, SOP step, approved or declined);
- the agent then continues the SOP: approved → run the action, re-check, finish; declined → the
  step's `declined` path (for example escalation) and nothing is changed.

## Memory

Sessions are stored by `SessionStore` (RAM) or `PgSessionStore` (Postgres, table `agent.session`,
chosen with `SESSION_STORE`). A session is saved when it is created, after every turn and when an
approval decision is logged (before the action runs). After a restart a session is loaded from the
database the first time it is asked for, including a pending approval, which can still be decided.
`GET /v1/sessions?customer_id=C1001` lists the customer's cases, newest first. Agents receive only
the last 12 messages (`MAX_AGENT_HISTORY`); the stored chat is complete.
## Agent hand-over

When a procedure ends with `end: handover` the agent reports it (an internal `handover` note in
its saved state). The orchestrator then picks the next agent: directly when the SOP names one
(`handover_to: inhome | access-pon`), or by the customer's carrier technology for
`access-network` (GPON / XGS-PON → `access-pon-agent`; the installed base is read from the
simulator in this demo). It emits `agent_handoff` (with the findings so far), calls the new agent
with that context and keeps it as the active agent for follow-ups. At most two hops per turn; if
no agent exists for the technology a `status_update` `handover_unavailable` explains why.
Approvals go to the agent that asked for them.
## Event stream of one turn

| Event | From | Meaning |
|---|---|---|
| `status_update` (`step=routing`) | orchestrator | Which agent was chosen and why |
| `agent_handoff` | orchestrator | The conversation moves to another agent |
| `status_update` (`step=diagnosis_started`) | agent | The agent started |
| `status_update` (`step=reasoning`, `round`) | agent | The model starts working (again after tool results) |
| `status_update` (`step=thinking`, `tokens`) | agent | Hidden reasoning progress, only if `LLM_THINKING=true` |
| `tool_call` (`started` / `completed`) | agent | A diagnostic tool runs, with its result |
| `agent_message_delta` | agent | Answer text, streamed token by token as the model writes it |
| `agent_message` (`final=true/false`) | agent | The complete message; `final=false` is narration before tool calls |
| `error` | either | Something failed; the turn still ends with `done` |
| `done` | orchestrator | End of the turn |

A `: heartbeat` comment is sent every 15 s of silence so slow CPU-only replies keep the
connection open. The `X-Correlation-ID` header (generated if missing) is forwarded to the agent and
set on every event.

Only completed exchanges are added to the session history, so a failed turn can be retried.
A second message while a turn is running returns `409`.

## Run and test

```bash
uv run python -m orchestrator               # port 8000; needs INHOME_AGENT_URL (default :8201)
uv run pytest services/orchestrator-agent   # includes a real-time streaming check over HTTP
```
