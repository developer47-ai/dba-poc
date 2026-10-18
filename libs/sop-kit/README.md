# sop-kit

Shared library for the knowledge base: the SOP document model plus everything needed to turn
Markdown files in [`knowledge/`](../../knowledge) into trustworthy, searchable content.

| Module | What it does |
|---|---|
| `models.py` | `SopDocument`, `Step` (actor, tool, consent, outcomes → next step, end), `Chunk` |
| `parser.py` | Reads YAML front matter, `##` sections and `### S1 — Title` procedure steps |
| `validator.py` | Checks ids, tools, consent for state-changing tools, and the step graph (reachable, can finish) |
| `chunker.py` | Summary chunk + section chunks (tables kept whole) + one chunk per step, each with a context line and metadata |
| `catalog.py` | Tools a step may use and whether they change the customer's service |

```bash
uv run python -m sop_kit knowledge   # validate the library (also runs in CI)
uv run pytest libs/sop-kit
```
