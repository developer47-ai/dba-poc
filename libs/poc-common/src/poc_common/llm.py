"""Factories for the local LLM (Ollama through its OpenAI-compatible API).

Requires the optional `llm` extra: `uv sync --extra llm` (installs langchain-openai).
"""

from typing import Any

from poc_common.config import Config, get_config
from poc_common.tracing import tracing_enabled


def get_chat_model(config: Config | None = None, **overrides: Any):
    from langchain_openai import ChatOpenAI

    config = config or get_config()
    params: dict[str, Any] = {
        "model": config.chat_model,
        "base_url": config.llm_base_url,
        "api_key": config.llm_api_key,
        "temperature": config.llm_temperature,
        "timeout": config.llm_timeout_s,
        "max_tokens": config.llm_max_tokens,
        "streaming": True,
    }
    if tracing_enabled():
        from poc_common.llm_trace import LlmSpanHandler

        params["callbacks"] = [LlmSpanHandler()]
        # Final stream chunk with token counts (Ollama supports it); shown on the llm.chat span.
        params["stream_usage"] = True
    if _thinking_off(config):
        # Ollama maps this to think=false. Without it Qwen3 reasons in a separate `reasoning`
        # channel that costs minutes per model call on a CPU, invisible to the user.
        params["reasoning_effort"] = "none"
    params.update(overrides)
    return ChatOpenAI(**params)


def get_embeddings(config: Config | None = None, **overrides: Any):
    from langchain_openai import OpenAIEmbeddings

    config = config or get_config()
    params: dict[str, Any] = {
        "model": config.embed_model,
        "base_url": config.llm_base_url,
        "api_key": config.llm_api_key,
        # Ollama expects plain strings, not the token arrays OpenAI clients send by default.
        "check_embedding_ctx_length": False,
    }
    params.update(overrides)
    return OpenAIEmbeddings(**params)


def strip_thinking(text: str) -> str:
    """Remove Qwen3 <think>...</think> blocks from a reply.

    Thinking-only models may omit the opening tag (the server adds it to the prompt), so text
    before a lone leading `</think>` is reasoning too.
    """
    import re

    text = re.sub(r"(?s)<think>.*?</think>", "", text)
    head, closing, tail = text.partition("</think>")
    if closing and "<think>" not in head:
        text = tail
    return text.strip()


NO_THINK_SWITCH = "/no_think"


def _thinking_off(config: Config) -> bool:
    """Whether to switch thinking off: only for Qwen3 models that can think (hybrid ones)."""
    model = config.chat_model.lower()
    return not config.llm_thinking and model.startswith("qwen3") and "instruct" not in model


def apply_thinking_mode(system_prompt: str, config: Config | None = None) -> str:
    """Append Qwen3's `/no_think` soft switch unless thinking is enabled.

    Qwen3 otherwise writes a long hidden reasoning block before every reply and tool call, which
    on a CPU costs minutes per turn while the user sees nothing. `get_chat_model` also turns
    thinking off at the API level; the prompt switch covers servers that ignore that setting.
    """
    config = config or get_config()
    if not _thinking_off(config):
        return system_prompt
    return f"{system_prompt.rstrip()}\n{NO_THINK_SWITCH}"


class ThinkingFilter:
    """Streaming version of `strip_thinking`: feed token chunks, get back only visible text.

    Handles tags split across chunks (e.g. "<thi" + "nk>") and drops leading whitespace so a
    streamed answer starts cleanly after a thinking block.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False
        self._started = False

    @property
    def thinking(self) -> bool:
        """True while the stream is inside a <think> block."""
        return self._inside

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        visible: list[str] = []
        while self._buffer:
            tag = self.CLOSE if self._inside else self.OPEN
            index = self._buffer.find(tag)
            if index >= 0:
                if not self._inside:
                    visible.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(tag):]
                self._inside = not self._inside
                continue
            # Hold back a possible partial tag at the end until the next chunk decides it.
            hold = next(
                (k for k in range(len(tag) - 1, 0, -1) if self._buffer.endswith(tag[:k])), 0
            )
            ready = self._buffer[: len(self._buffer) - hold]
            self._buffer = self._buffer[len(self._buffer) - hold:]
            if not self._inside:
                visible.append(ready)
            break
        return self._emit("".join(visible))

    def flush(self) -> str:
        rest = "" if self._inside else self._buffer
        self._buffer = ""
        return self._emit(rest)

    def _emit(self, text: str) -> str:
        if not self._started:
            text = text.lstrip()
            self._started = bool(text)
        return text
