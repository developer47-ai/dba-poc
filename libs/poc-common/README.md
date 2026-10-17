# poc-common

Shared building blocks used by every DBA POC service.

| Module | Purpose |
|---|---|
| `poc_common.config` | `Config` (pydantic-settings): LLM endpoint and models, database/cache URLs, MCP and agent service URLs. Environment variables override defaults; a `.env` file is read if present. |
| `poc_common.logging` | JSON logging plus a correlation id and session id carried through `contextvars`; `X-Correlation-ID` is the HTTP header name. |
| `poc_common.events` | `Event` / `EventType` streamed to the UI over SSE (`to_sse`, `parse_sse`), and the human-approval models `ConsentRequest` / `ConsentResponse`. |
| `poc_common.mcp_client` | `call_tool`, `list_tools`, `mcp_session` for MCP servers over streamable HTTP; forwards the correlation id. |
| `poc_common.llm` | `get_chat_model`, `get_embeddings` (LangChain clients for Ollama's OpenAI-compatible API) and `strip_thinking`. Needs the `llm` extra. |

## Develop

```bash
uv sync --python 3.12 --extra llm --all-packages
uv run pytest libs/poc-common
uv run ruff check libs
```

The MCP tests start a real in-process MCP server, so they exercise the actual protocol.

The `mcp` dependency is pinned below 2.0, which renamed `FastMCP` and changed the client API.
