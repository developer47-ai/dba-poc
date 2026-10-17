"""Factories for the local LLM (Ollama through its OpenAI-compatible API).

Requires the optional `llm` extra: `uv sync --extra llm` (installs langchain-openai).
"""

from typing import Any

from poc_common.config import Config, get_config


def get_chat_model(config: Config | None = None, **overrides: Any):
    from langchain_openai import ChatOpenAI

    config = config or get_config()
    params: dict[str, Any] = {
        "model": config.chat_model,
        "base_url": config.llm_base_url,
        "api_key": config.llm_api_key,
        "temperature": config.llm_temperature,
        "timeout": config.llm_timeout_s,
    }
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
    """Remove Qwen3 <think>...</think> blocks from a reply."""
    import re

    return re.sub(r"(?s)<think>.*?</think>", "", text).strip()
