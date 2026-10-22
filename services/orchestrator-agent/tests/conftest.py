import asyncio
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from inhome_agent import create_app as create_inhome_app
from knowledge_mcp.testing import run_test_knowledge_server
from network_diagnostics_mcp import create_server
from network_simulator import Network
from network_simulator import create_app as create_simulator_app
from orchestrator import create_app
from orchestrator.routing import AgentRegistry
from poc_common.config import Config
from poc_common.events import Event, EventType
from poc_common.fake_llm import ScriptedChatModel
from poc_common.testing import run_app_in_thread

ROOT = Path(__file__).resolve().parents[3]
SCENARIO_DIR = ROOT / "data" / "scenarios"


@pytest.fixture(scope="module")
def simulator_url():
    with run_app_in_thread(create_simulator_app(Network.from_directory(SCENARIO_DIR))) as url:
        yield url


@pytest.fixture(scope="module")
def mcp_url(simulator_url):
    with run_app_in_thread(create_server(simulator_url).streamable_http_app()) as base:
        yield f"{base}/mcp"


@pytest.fixture(scope="module")
def knowledge_url():
    with run_test_knowledge_server(ROOT / "knowledge") as url:
        yield url


@pytest.fixture
def inhome(mcp_url, knowledge_url):
    """Start a real In-Home agent with a scripted model: url, llm = inhome(reply1, reply2...)."""
    with ExitStack() as stack:

        def start(*replies):
            llm = ScriptedChatModel(script=list(replies))
            config = Config(diagnostics_mcp_url=mcp_url, knowledge_mcp_url=knowledge_url)
            return stack.enter_context(run_app_in_thread(create_inhome_app(config, llm))), llm

        yield start


@pytest.fixture
def fake_agent():
    """Start a minimal agent: url, calls = fake_agent(delay=..., status=..., state=...)."""
    with ExitStack() as stack:

        def start(*, delay: float = 0.0, status: int = 200, state: dict | None = None,
                  ask_consent: dict | None = None):
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
                    decided = calls[-1]["json"].get("consent") is not None
                    if ask_consent is not None and not decided:
                        yield Event(type=EventType.CONSENT_REQUEST, source="inhome-agent",
                                    data=ask_consent).to_sse()
                        yield Event(type=EventType.AGENT_STATE, source="inhome-agent", data={
                            "state": {"sop": {"awaiting": "consent",
                                              "consent_id": ask_consent["consent_id"]}}}).to_sse()
                    elif ask_consent is not None:
                        yield Event(type=EventType.AGENT_STATE, source="inhome-agent",
                                    data={"state": {"sop": None}}).to_sse()
                    elif state is not None:
                        yield Event(type=EventType.AGENT_STATE, source="inhome-agent",
                                    data={"state": state}).to_sse()
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


@pytest.fixture
def orchestrator_with_agents():
    """Orchestrator wired to several agents: client = start({name: url}, carrier_tech="GPON")."""
    with ExitStack() as stack:

        def start(agents: dict[str, str], carrier_tech: str | None = None) -> TestClient:
            app = create_app(Config(), registry=AgentRegistry(agents))

            async def lookup(customer_id: str) -> str | None:
                return carrier_tech

            app.state.router.carrier_tech = lookup
            return stack.enter_context(TestClient(app))

        yield start