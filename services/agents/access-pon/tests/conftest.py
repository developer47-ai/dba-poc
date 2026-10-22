import os
from pathlib import Path

import httpx
import pytest

from knowledge_mcp.testing import run_test_knowledge_server
from network_diagnostics_mcp import create_server
from network_simulator import Network, create_app
from poc_common.config import Config
from poc_common.testing import run_app_in_thread
from scheduling_mcp import Scheduler
from scheduling_mcp import create_server as create_scheduling_server

ROOT = Path(__file__).resolve().parents[4]
SCENARIO_DIR = ROOT / "data" / "scenarios"
LIBRARY = ROOT / "knowledge"


@pytest.fixture(scope="module")
def simulator_url():
    with run_app_in_thread(create_app(Network.from_directory(SCENARIO_DIR))) as url:
        yield url


@pytest.fixture(scope="module")
def mcp_url(simulator_url):
    with run_app_in_thread(create_server(simulator_url).streamable_http_app()) as base:
        yield f"{base}/mcp"


@pytest.fixture(scope="module")
def scheduling_url():
    server = create_scheduling_server(Scheduler())
    with run_app_in_thread(server.streamable_http_app()) as base:
        yield base


@pytest.fixture(autouse=True)
def fresh_state(simulator_url, scheduling_url):
    httpx.post(f"{simulator_url}/admin/reset").raise_for_status()
    httpx.post(f"{scheduling_url}/admin/reset").raise_for_status()


@pytest.fixture(scope="session")
def knowledge_url():
    with run_test_knowledge_server(LIBRARY) as url:
        yield url


@pytest.fixture
def config(mcp_url, knowledge_url, scheduling_url) -> Config:
    return Config(
        diagnostics_mcp_url=mcp_url,
        knowledge_mcp_url=knowledge_url,
        scheduling_mcp_url=f"{scheduling_url}/mcp",
    )


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_LLM_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="needs Ollama; set RUN_LLM_TESTS=1")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)