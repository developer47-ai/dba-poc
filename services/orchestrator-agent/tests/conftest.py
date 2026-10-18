import asyncio
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from inhome_agent import create_app as create_inhome_app
from network_diagnostics_mcp import create_server
from network_simulator import Network
from network_simulator import create_app as create_simulator_app
from orchestrator import create_app
from orchestrator.routing import AgentRegistry
from poc_common.config import Config
from poc_common.events import Event, EventType
from poc_common.fake_llm import ScriptedChatModel
from poc_common.testing import run_app_in_thread

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "data" / "scenarios"


@pytest.fixture(scope="module")
def mcp_url():
    network = Network.from_directory(SCENARIO_DIR)
    with run_app_in_thread(create_simulator_app(network)) as simulator_url:
        with run_app_in_thread(create_server(simulator_url).streamable_http_app()) as base:
            yield f"{base}/mcp"


@pytest.fixture
def inhome(mcp_url):
    """Start a real In-Home agent with a scripted model: url, llm = inhome(reply1, reply2...)."""
    with ExitStack() as stack:

        def start(*replies):
            llm = ScriptedChatModel(script=list(replies))
            app = create_inhome_app(Config(diagnostics_mcp_url=mcp_url), llm)
            return stack.enter_context(run_app_in_thread(app)), llm

        yield start


@pytest.fixture
def fake_agent():
    """Start a minimal agent: url, calls = fake_agent(delay=..., status=...)."""
    with ExitStack() as stack:

        def start(*, delay: float = 0.0, status: int = 200):
            calls: list[dict] = []
            app = FastAPI()

            @app.post("/v1/diagnose")
            async def diagnose(request: Request):
                calls.append({"json": await request.json(), "headers": dict(request.headers)})
                if status != 200:
                    return JSONResponse({"detail": "agent crashed"}, status_code=status)

                async def body():
                    yield Event(type=EventType.AGENT_MESSAGE_DELTA, source="inhome-agent",
                                data={"message_id": "m1", "text": "late "}).to_sse()
                    await asyncio.sleep(delay)
                    yield Event(type=EventType.AGENT_MESSAGE_DELTA, source="inhome-agent",
                                data={"message_id": "m1", "text": "answer"}).to_sse()
                    yield Event(type=EventType.AGENT_MESSAGE, source="inhome-agent",
                                data={"message_id": "m1", "text": "late answer",
                                      "final": True}).to_sse()
                    yield Event(type=EventType.DONE, source="inhome-agent").to_sse()

                return StreamingResponse(body(), media_type="text/event-stream")

            return stack.enter_context(run_app_in_thread(app)), calls

        yield start


@pytest.fixture
def orchestrator():
    """Open an orchestrator client wired to one agent url: client = orchestrator(url)."""
    with ExitStack() as stack:

        def start(agent_url: str, heartbeat_s: float = 15.0) -> TestClient:
            app = create_app(
                Config(), registry=AgentRegistry({"inhome-agent": agent_url}),
                heartbeat_s=heartbeat_s,
            )
            return stack.enter_context(TestClient(app))

        yield start
