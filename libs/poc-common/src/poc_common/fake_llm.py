"""Scripted chat model for tests: replays prepared AI messages so agents run without an LLM.

Requires the optional `llm` extra (langchain-core).
"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted `AIMessage`s in order; records what it was asked."""

    script: list[AIMessage]
    received: list[list[BaseMessage]] = Field(default_factory=list)
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        if self.cursor >= len(self.script):
            raise AssertionError("ScriptedChatModel ran out of scripted replies")
        self.received.append(list(messages))
        reply = self.script[self.cursor]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=reply)])


def tool_call_message(name: str, args: dict[str, Any] | None = None, call_id: str = "call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])
