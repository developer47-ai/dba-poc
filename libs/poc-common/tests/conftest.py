import pytest

from poc_common.config import Config


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # Run each test in an empty directory so a local .env cannot leak into the results.
    monkeypatch.chdir(tmp_path)
    for name in ("OLLAMA_CHAT_MODEL", "CHAT_MODEL", "LLM_BASE_URL", "DIAGNOSTICS_MCP_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config() -> Config:
    return Config()
