from poc_common.config import Config


def test_defaults_target_local_ollama(config):
    assert config.llm_base_url == "http://localhost:11434/v1"
    assert config.chat_model == "qwen3:4b-instruct-2507-q4_K_M"
    assert config.embed_model == "bge-m3"
    assert config.llm_temperature == 0.0


def test_ollama_model_variable_overrides_default(monkeypatch):
    monkeypatch.setenv("OLLAMA_CHAT_MODEL", "qwen3:8b")
    assert Config().chat_model == "qwen3:8b"


def test_service_urls_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DIAGNOSTICS_MCP_URL", "http://diagnostics-mcp:8101/mcp")
    assert Config().diagnostics_mcp_url == "http://diagnostics-mcp:8101/mcp"


def test_each_mcp_server_has_a_distinct_port(config):
    urls = [
        config.diagnostics_mcp_url,
        config.installed_base_mcp_url,
        config.scheduling_mcp_url,
        config.ticketing_mcp_url,
        config.knowledge_mcp_url,
    ]
    assert len(set(urls)) == len(urls)
