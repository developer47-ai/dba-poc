# knowledge-mcp

MCP server over the NetCo knowledge library: hybrid search for knowledge questions and
deterministic, step-by-step navigation of troubleshooting procedures (SOPs). All tools are
read-only.

| Tool | Purpose |
|---|---|
| `search_knowledge(query, agent?, carrier_tech_type?, kind?, top_k=4)` | Cited passages from procedures and reference documents |
| `find_troubleshooting_sop(symptom, agent, carrier_tech_type?)` | `found` + up to three procedures with their entry step; `found: false` when nothing applies |
| `get_sop_procedure(doc_id)` | All steps: actor, instruction, tool, approval needed, outcomes, end |
| `get_sop_step(doc_id, step_id)` | One step |
| `get_next_sop_step(doc_id, step_id, outcome)` | The next step for the observed outcome; invalid outcomes list the valid ones |

## How search works

1. **Keyword search**: Postgres full-text search (English stemming) or BM25 in memory: exact
   terms such as "LOS" or "PPP".
2. **Vector search**: bge-m3 embeddings in pgvector (HNSW, cosine): meaning, even without shared
   words.
3. **Reciprocal Rank Fusion** merges both rankings (`1 / (60 + rank)` from each).
4. **Relevance gate**: a result counts only if it matched a keyword or its similarity is at least
   `KB_MIN_SIMILARITY` (0.45), so off-topic questions return nothing instead of noise.
5. **Filters**: agent scope, carrier technology, document kind.

Content is loaded by the one-shot `kb-ingestion` job (`python -m knowledge_base.ingest`), which
validates the library, embeds new or changed documents and removes retired ones.

HTTP routes for demos and checks: `GET /health`, `GET /v1/documents`,
`GET /v1/search?q=...&agent=...&tech=...&kind=...`.

```bash
uv run pytest services/mcp/knowledge libs/knowledge-base      # in-memory store
RUN_PG_TESTS=1 uv run pytest libs/knowledge-base               # also Postgres + pgvector
```
