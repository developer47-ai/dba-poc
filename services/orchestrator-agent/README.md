# orchestrator-agent

Entry point for the support expert's conversation. It keeps sessions, decides which specialist
agent handles each turn, and relays the agent's events to the chat UI **in real time** over
Server-Sent Events (SSE).

## API

```http
POST /v1/sessions                       { "customer_id": "C1002", "expert_name": "Sam" }   -> 201
GET  /v1/sessions/{session_id}          session summary and history
POST /v1/sessions/{session_id}/messages { "message": "Customer has no internet" }        -> text/event-stream
GET  /health
```

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
