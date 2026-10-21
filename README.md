# DBA POC — Local Multi-Agent Connectivity Troubleshooting Assistant

[![CI](https://github.com/developer47-ai/dba-poc/actions/workflows/ci.yml/badge.svg)](https://github.com/developer47-ai/dba-poc/actions/workflows/ci.yml)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/developer47-ai/dba-poc?quickstart=1)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A fully local, open-source proof of concept of an **enterprise AI agent platform**.
A customer support expert chats with an AI assistant that diagnoses a customer's
*"no internet"* problem end-to-end: it measures the line, routes the case to the right
specialist agent, follows the official troubleshooting procedure (SOP), asks for human
approval before risky actions, and books an engineer or opens a ticket when needed.

Everything runs on a laptop with `docker compose` — no cloud, no hosted LLM, no licensed tools.

> **Disclaimer** — This is a clean-room demo. The telecom operator **"NetCo"**, its customers,
> devices, procedures and backend systems are fictional and simulated. No proprietary code,
> prompts or data from any company are included.

---

## What it demonstrates

| Capability | How it shows up in the product |
|---|---|
| Multi-agent architecture | Orchestrator + In-Home, Access-PON, Access-P2P and Access-Copper specialist agents |
| Agent orchestration | Carrier-aware routing, agent hand-over and fallback reasoning in LangGraph |
| MCP | Five MCP servers (diagnostics, installed base, scheduling, ticketing, knowledge) over streamable HTTP |
| Tool calling | Agents call diagnostic and fulfilment tools on a simulated network |
| RAG & knowledge search | Hybrid (keyword + vector) search over SOP documents with citations |
| Document processing | SOPs (Markdown / PDF / DOCX) parsed and chunked with Docling |
| Workflow execution | SOP procedures executed step by step as a deterministic graph |
| Human-in-the-loop | Expert approves device reboots, Wi-Fi resets, engineer visits and swap orders |
| Memory | Conversation checkpoints, session context and long-term customer memory |
| Observability | LLM/agent traces in Langfuse, distributed traces in Jaeger |
| Local LLM | Qwen3 + bge-m3 served by Ollama (CPU or GPU) |

## Architecture

```mermaid
flowchart LR
    CE(["Support Expert"]) -->|"request"| UI["chat-ui"]
    UI -->|"REST"| ORC["orchestrator-agent"]
    ORC --> QS["quickscan-service"]
    ORC --> IH["inhome-agent"]
    ORC --> PON["access-pon-agent"]
    ORC --> P2P["access-p2p-agent"]
    ORC --> CU["access-copper-agent"]
    subgraph MCP["MCP servers"]
        DIAG["network-diagnostics-mcp"]
        IB["installed-base-mcp"]
        SCH["scheduling-mcp"]
        TIC["ticketing-mcp"]
        KB["knowledge-mcp"]
    end
    QS --> IB
    QS --> DIAG
    IH --> DIAG
    IH --> KB
    PON --> DIAG
    PON --> KB
    P2P --> KB
    CU --> KB
    ORC --> SCH
    ORC --> TIC
    DIAG --> SIM["network-simulator"]
    KB --> PG[("postgres + pgvector")]
    ORC --> LLM["ollama"]
    ORC ==>|"SSE response"| UI
    UI ==>|"answer + approvals"| CE
```

Detailed diagrams and design decisions will be added under `docs/` as the steps progress.

## Repository layout (target)

```text
dba-poc/
├── services/
│   ├── chat-ui/                      # Streamlit expert console
│   ├── orchestrator-agent/           # FastAPI + LangGraph supervisor, SSE
│   ├── quickscan-service/            # Initial line measurements
│   ├── agents/                       # inhome | access-pon | access-p2p | access-copper
│   ├── mcp/                          # network-diagnostics | installed-base | scheduling | ticketing | knowledge
│   ├── network-simulator/            # Stateful fake network with scenario customers
│   └── kb-ingestion/                 # Docling SOP ingestion worker
├── libs/poc-common/                  # Shared: settings, LLM factory, MCP client, events, logging
├── knowledge/sop/                    # SOP documents (source of truth for RAG)
├── data/scenarios/                   # Fictional customers and fault scenarios
├── infra/                            # Postgres init, Langfuse, Traefik, Keycloak config
├── evaluation/                       # Golden question sets and RAG/agent evaluation
├── scripts/                          # setup / demo helpers (PowerShell + bash)
├── docs/                             # Architecture, ADRs, demo script
└── docker-compose.yml
```

## Quick start

```bash
git clone https://github.com/developer47-ai/dba-poc.git
cd dba-poc
docker compose up -d            # first run builds the images and downloads ~3.5 GB of models
```

Open **http://localhost:8501**, pick scenario customer **C1002** in the sidebar, click
**Start new session** and type *"The customer has no internet"*. The In-Home agent runs its
diagnostic tools (visible live in the activity panel) and streams its answer word by word.
**Reset customer's network** restores the scenario between demos.

Checks: `./scripts/smoke-test.sh` (Windows: `.\scripts\smoke-test.ps1`) tests the platform, and
`python scripts/e2e_check.py` runs a full troubleshooting conversation through the API.

All settings have working defaults. To override them (for example `OLLAMA_CHAT_MODEL=qwen3:8b`),
create a `.env` file next to `docker-compose.yml`; see the variables used in the compose file.
NVIDIA GPU users: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`.

**Speed on CPU.** The default chat model is **Qwen3-4B Instruct-2507**
(`qwen3:4b-instruct-2507-q4_K_M`), which answers directly. The plain `qwen3:4b` tag is the
*thinking-only* variant: it writes a long hidden reasoning before every step and is several times
slower on a CPU. For hybrid models such as `qwen3:8b`, thinking is switched off unless
`LLM_THINKING=true`. Ollama uses one request slot and an 8k context (`OLLAMA_NUM_PARALLEL`,
`OLLAMA_CONTEXT_LENGTH`), and the chat model is loaded into memory during startup. Every CI run
publishes the measured time to first tool call, first answer token and whole turn on its run page.

| Service | Port (localhost only) | Purpose |
|---|---|---|
| Chat UI (Streamlit) | 8501 | Expert console |
| Orchestrator | 8000 | Sessions and streamed turns (`/docs` for the API) |
| Knowledge MCP | 8105 | SOP search and navigation; try `/v1/search?q=LOS+light+red` |
| Ollama | 11434 | Local LLM, OpenAI-compatible API at `/v1` |
| PostgreSQL + pgvector | 5432 | Data and vector search (user `dba`, db `dba`) |
| Valkey | 6379 | Cache and rate limits |

In-Home agent, diagnostics MCP server and network simulator run on the internal Docker network only.

**Requirements:** a Docker-compatible runtime (Rancher Desktop, Docker Engine or Podman),
16 GB RAM minimum (32 GB recommended), ~25 GB free disk. A GPU is optional.

### Ways to run it

| Where | How | Best for |
|---|---|---|
| **GitHub Codespaces** (no install) | Click the *Open in GitHub Codespaces* badge (4 cores / 16 GB), run `docker compose up -d` in its terminal, then open the forwarded port 8501 | Trying it from any browser |
| **Your machine with Docker** | The commands above | Full speed, recording demos |
| **Python only, no Docker** | `uv sync --python 3.12 --extra llm --all-packages` then `uv run pytest` | Developing services; tests use a scripted model |

Every push is also checked by [CI](.github/workflows/ci.yml): lint and tests, then the whole
product is built and started with the real local model, and an end-to-end troubleshooting
conversation must stream a correct answer.

## Roadmap

Built step by step; each step is one pull request and ends in something you can run.

### Phase A — Foundation
- [x] **Step 0 — Repository foundation**: license, git hygiene, secret scanning, roadmap
- [x] **Step 1 — Local platform**: `docker-compose.yml` with Ollama, Postgres + pgvector, Valkey; automatic model pull; health checks; smoke test
- [x] **Step 2 — Shared library** `poc-common`: config, OpenAI-compatible LLM factory, MCP client, event models, structured logging with correlation IDs

### Phase B — Walking skeleton → **M1: first end-to-end troubleshooting in chat** ✅
- [x] **Step 3 — Network simulator + `network-diagnostics-mcp`** with scenario customers
- [x] **Step 4 — `inhome-agent`** (LangGraph agent with MCP tool calling, read-only, SSE stream)
- [x] **Step 5 — `orchestrator-agent`** (FastAPI, sessions, routing, real-time SSE relay with token streaming)
- [x] **Step 6 — `chat-ui`** (Streamlit console, live tool activity, token streaming) + all services in `docker compose` + end-to-end CI

### Phase C — Knowledge & process → **M2: SOP-guided troubleshooting with citations**
- [x] **Step 7 — SOP library + `kb-ingestion` + `knowledge-mcp`** (hybrid search, SOP step graph)
  - [x] 7a — SOP format, [`knowledge/`](knowledge) library (8 procedures, 2 references), `sop-kit` parser, validator and chunker; validated in CI
  - [x] 7b — Postgres/pgvector store, idempotent embedding ingestion, hybrid search (BM25/full-text + vectors, RRF, relevance gate), `knowledge-mcp` with deterministic SOP navigation
- [x] **Step 8 — SOP-guided agents** (follow steps, cite `sop_id/step_id`, stop for approval, no-SOP fallback)

### Phase D — Full multi-agent platform → **M3: all scenarios**
- [ ] **Step 9 — `installed-base-mcp` + `quickscan-service`**
- [ ] **Step 10 — Access PON / P2P / Copper agents**, carrier routing and agent hand-over
- [x] **Step 11 — Human-in-the-loop** (minimal): Approve / Decline buttons, audited decisions, the approved action really runs and the SOP continues; shown on two scenarios (C1002 reboot, C1003 Wi-Fi reset) plus the decline path. `scheduling-mcp` / `ticketing-mcp` are left for later.
- [ ] **Step 12 — Memory**: Postgres checkpointer, session cache, customer memory

### Phase E — Operate & share → **v0.1.0 release**
- [ ] **Step 13 — Observability**: Langfuse, OpenTelemetry, Jaeger, feedback scores
- [ ] **Step 14 — Guardrails & identity**: input guardrails, scope gate, optional Traefik + Keycloak
- [ ] **Step 15 — Evaluation & CI**: golden sets, RAG metrics, GitHub Actions
- [ ] **Step 16 — Visitor packaging**: one-command setup, demo script, screenshots, release

## Planned demo scenarios

Fictional customers live in [`data/scenarios/`](data/scenarios); the
[network simulator](services/network-simulator) keeps their live state and the
[diagnostics MCP server](services/mcp/network-diagnostics) exposes 12 tools on top of it
(9 read-only, 3 that change state and will need human approval).

| Customer | Fault | Agents involved | Remote fix? | Human approval |
|---|---|---|---|---|
| C1001 fiber GPON, LOS red | No optical signal | Access-PON | No | Engineer appointment |
| C1002 fiber XGSPON, modem stuck | PPP session stuck | In-Home | Reboot modem | Remote reboot |
| C1003 fiber GPON, Wi-Fi changed | Wrong Wi-Fi settings | In-Home | Reset Wi-Fi | Wi-Fi reset |
| C1004 copper VDSL, modem faulty | Hardware fault | Access-Copper → In-Home | No | Modem swap order |
| C1005 fiber EoF, port down | Access port down | Access-P2P | No | Incident ticket |
| C1006 fiber GPON, healthy | None (control) | In-Home | – | – |

## Tech stack

| Layer | Technology | License |
|---|---|---|
| LLM runtime | Ollama (Qwen3, bge-m3) | MIT / Apache-2.0 |
| Agents & workflows | LangGraph, LangChain | MIT |
| Tool protocol | MCP Python SDK / FastMCP | MIT |
| API | FastAPI | MIT |
| UI | Streamlit | Apache-2.0 |
| Database & vectors | PostgreSQL + pgvector | PostgreSQL |
| Cache | Valkey | BSD-3 |
| Document processing | Docling | MIT |
| Observability | Langfuse, OpenTelemetry, Jaeger | MIT / Apache-2.0 |
| Gateway & identity (optional) | Traefik, Keycloak | MIT / Apache-2.0 |

## Contributing

Python code uses [uv](https://docs.astral.sh/uv/) and Python 3.12:

```bash
uv sync --python 3.12 --extra llm --all-packages
uv run pytest
uv run ruff check .
```

1. `pip install pre-commit && pre-commit install` — blocks secrets and large files.
2. One step = one branch = one pull request.
3. Never commit real customer data, company code, internal URLs or credentials.

## License

[MIT](LICENSE) © 2026 Ayush Kumar
