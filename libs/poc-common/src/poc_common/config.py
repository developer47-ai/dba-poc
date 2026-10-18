"""Central configuration: every service reads the same environment variables."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    service_name: str = "poc-service"
    log_level: str = "INFO"
    log_json: bool = True

    # LLM: any OpenAI-compatible endpoint; the POC uses Ollama.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    # Qwen3-4B Instruct-2507: answers directly (no hidden reasoning), good tool calling, 2.3 GB.
    # Note: the plain "qwen3:4b" tag is the thinking-only 2507 variant, which is slow on CPU.
    chat_model: str = Field(
        default="qwen3:4b-instruct-2507-q4_K_M",
        validation_alias=AliasChoices("OLLAMA_CHAT_MODEL", "CHAT_MODEL"),
    )
    embed_model: str = Field(
        default="bge-m3", validation_alias=AliasChoices("OLLAMA_EMBED_MODEL", "EMBED_MODEL")
    )
    llm_temperature: float = 0.0
    llm_timeout_s: float = 300.0
    # Hidden reasoning ("thinking") makes small models slower by minutes on CPU; off by default.
    llm_thinking: bool = False
    llm_max_tokens: int | None = 800

    # Data stores
    database_url: str = "postgresql://dba:dba_local_pw@localhost:5432/dba"
    valkey_url: str = "redis://localhost:6379/0"

    # Backend systems (simulated)
    network_simulator_url: str = "http://localhost:8300"

    # MCP servers (streamable HTTP)
    diagnostics_mcp_url: str = "http://localhost:8101/mcp"
    installed_base_mcp_url: str = "http://localhost:8102/mcp"
    scheduling_mcp_url: str = "http://localhost:8103/mcp"
    ticketing_mcp_url: str = "http://localhost:8104/mcp"
    knowledge_mcp_url: str = "http://localhost:8105/mcp"

    # Agent services (HTTP + SSE)
    orchestrator_url: str = "http://localhost:8000"
    quickscan_url: str = "http://localhost:8200"
    inhome_agent_url: str = "http://localhost:8201"
    access_pon_agent_url: str = "http://localhost:8202"
    access_p2p_agent_url: str = "http://localhost:8203"
    access_copper_agent_url: str = "http://localhost:8204"


@lru_cache
def get_config() -> Config:
    return Config()
