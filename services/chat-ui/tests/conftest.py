import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from network_simulator import Network
from network_simulator import create_app as create_simulator_app
from orchestrator import create_app as create_orchestrator_app
from orchestrator.routing import AgentRegistry
from poc_common.config import Config
from poc_common.events import Event, EventType
from poc_common.testing import run_app_in_thread

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "data" / "scenarios"
ANSWER = "Findings: the PPP session is down. Recommended next step: reboot the modem."


def agent_events() -> list[Event]:
    def e(type_, **data):
        return Event(type=type_, source="inhome-agent", data=data)

    words = [w + " " for w in ANSWER.split(" ")]
    words[-1] = words[-1].rstrip()
    return [
        e(EventType.STATUS_UPDATE, step="diagnosis_started", tools=9),
        e(EventType.TOOL_CALL, tool="get_ppp_session", arguments={}, status="started"),
        e(EventType.TOOL_CALL, tool="get_ppp_session", status="completed",
          result={"status": "down", "last_change": "2026-10-17T18:40:00Z"}),
        *[e(EventType.AGENT_MESSAGE_DELTA, message_id="m1", text=w) for w in words],
        e(EventType.AGENT_MESSAGE, message_id="m1", text=ANSWER, final=True),
        e(EventType.DONE),
    ]


def fake_agent_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/diagnose")
    async def diagnose():
        async def body():
            for event in agent_events():
                await asyncio.sleep(0.01)
                yield event.to_sse()

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


@pytest.fixture
def answer() -> str:
    return ANSWER


@pytest.fixture(scope="module")
def orchestrator_url():
    with (
        run_app_in_thread(create_simulator_app(Network.from_directory(SCENARIO_DIR))) as sim_url,
        run_app_in_thread(fake_agent_app()) as agent_url,
    ):
        app = create_orchestrator_app(
            Config(network_simulator_url=sim_url),
            registry=AgentRegistry({"inhome-agent": agent_url}),
        )
        with run_app_in_thread(app) as url:
            yield url
