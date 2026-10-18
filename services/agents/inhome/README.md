# inhome-agent

Specialist agent for problems inside the customer's home: modem, indicator lights, Wi-Fi settings
and connected devices. It is a small LangGraph (`agent` ⇄ `tools`) that calls the
network-diagnostics MCP tools and answers the support expert.

- **Read-only.** Only tools marked `readOnlyHint` are offered. Reboot and Wi-Fi reset are added
  with the human-approval flow (Step 11).
- **Customer scoped.** The code injects `customer_id` into every tool call and hides it from the
  model's tool schema.
- **Streams in real time** as SSE `Event`s: `status_update`, `tool_call` (started / completed),
  `agent_message_delta` (answer tokens as they are generated, Qwen3 `<think>` text filtered out),
  `agent_message` (complete text, `final` true for the answer), `error`, `done`.

## API

```http
POST /v1/diagnose            # text/event-stream
{ "customer_id": "C1002", "message": "Customer has no internet", "session_id": "s1", "history": [] }

GET /health
```

## Run and test

```bash
uv run python -m inhome_agent                 # port 8201; needs the diagnostics MCP server
uv run pytest services/agents                 # scripted model, no Ollama needed
RUN_LLM_TESTS=1 uv run pytest services/agents # also runs the live-model test (needs Ollama)
```
