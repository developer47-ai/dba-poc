"""LangChain callback that records every model call as a span (`llm.chat`) in the current trace.

Shows in Jaeger which model ran, how long it took until the first token and in total, how many
messages went in, how much came out and, when the server reports it, the token counts.
Requires the optional `llm` extra (langchain-core).
"""

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry.trace import Span

from poc_common.tracing import fail, get_tracer


class LlmSpanHandler(BaseCallbackHandler):
    # Run inline so the span is created in the caller's context and becomes a child of its span.
    run_inline = True

    def __init__(self, system: str = "ollama") -> None:
        self.system = system
        self._runs: dict[UUID, dict[str, Any]] = {}

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[Any]], *, run_id: UUID,
        invocation_params: dict[str, Any] | None = None, **kwargs: Any,
    ) -> None:
        params = invocation_params or {}
        flat = [m for batch in messages for m in batch]
        span = get_tracer().start_span("llm.chat", attributes={
            "gen_ai.system": self.system,
            "gen_ai.request.model": str(params.get("model") or params.get("model_name") or ""),
            "llm.messages": len(flat),
            "llm.prompt_chars": sum(len(str(m.content)) for m in flat),
        })
        self._runs[run_id] = {"span": span, "start": time.monotonic(), "chunks": 0}

    def on_llm_new_token(self, token: str, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        run["chunks"] += 1
        if run["chunks"] == 1:
            run["span"].set_attribute(
                "llm.first_token_ms", round((time.monotonic() - run["start"]) * 1000)
            )

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        span: Span = run["span"]
        message = response.generations[0][0].message if response.generations else None
        if message is not None:
            span.set_attribute("llm.completion_chars", len(str(message.content)))
            span.set_attribute("llm.tool_calls", len(getattr(message, "tool_calls", None) or []))
            usage = getattr(message, "usage_metadata", None) or {}
            for source, target in (("input_tokens", "gen_ai.usage.input_tokens"),
                                   ("output_tokens", "gen_ai.usage.output_tokens")):
                if usage.get(source) is not None:
                    span.set_attribute(target, int(usage[source]))
        if run["chunks"]:
            span.set_attribute("llm.stream_chunks", run["chunks"])
        span.end()

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        run["span"].record_exception(error)
        fail(run["span"], error)
        run["span"].end()
