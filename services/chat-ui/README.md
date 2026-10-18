# chat-ui

The expert console: a Streamlit chat where a support expert picks a (scenario) customer, starts a
session and troubleshoots with the agents.

- **Live activity panel**: routing, agent hand-overs and every diagnostic tool call with a short
  result, as they happen.
- **Token streaming**: the answer appears word by word while the model writes it.
- **Demo controls**: scenario customer picker and *Reset customer's network* to undo reboots/resets.
- **Local-first**: Streamlit usage telemetry is switched off.

## Structure

| Module | Role |
|---|---|
| `client.py` | Synchronous orchestrator client; `stream_turn` yields events as they arrive |
| `view.py` | Pure reducer `apply_event(TurnView, Event)`: what to show for each event (unit-tested) |
| `app.py` | Thin Streamlit page on top of the two |

## Run and test

```bash
uv run python -m chat_ui              # http://localhost:8501, needs ORCHESTRATOR_URL (default :8000)
uv run pytest services/chat-ui        # includes a headless run of the real page (Streamlit AppTest)
```
