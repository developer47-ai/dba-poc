"""Scripted chat model for tests: replays prepared AI messages so agents run without an LLM.

It streams like a real model (word chunks, then tool-call chunks), so streaming code paths are
exercised too. Requires the optional `llm` extra (langchain-core).
"""

import json
import re
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted `AIMessage`s in order; records what it was asked."""

    script: list[AIMessage]
    received: list[list[BaseMessage]] = Field(default_factory=list)
    cursor: int = 0
    token_delay_s: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        if self.cursor >= len(self.script):
            raise AssertionError("ScriptedChatModel ran out of scripted replies")
        self.received.append(list(messages))
        reply = self.script[self.cursor]
        self.cursor += 1
        return reply

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs: Any):
        reply = self._next(messages)
        message_id = reply.id or f"scripted-{self.cursor}"
        text = reply.content if isinstance(reply.content, str) else ""
        for piece in re.findall(r"\S+\s*|\s+", text):
            if self.token_delay_s:
                time.sleep(self.token_delay_s)
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece, id=message_id))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk
        tool_chunks = [
            {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
            for i, c in enumerate(reply.tool_calls)
        ]
        if tool_chunks or not text:
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(content="", id=message_id, tool_call_chunks=tool_chunks)
            )
            if run_manager:
                run_manager.on_llm_new_token("", chunk=chunk)
            yield chunk


def tool_call_message(name: str, args: dict[str, Any] | None = None, call_id: str = "call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])
