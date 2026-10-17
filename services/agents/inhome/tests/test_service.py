import os

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from inhome_agent import create_app
from poc_common.config import Config
from poc_common.events import EventType, parse_sse
from poc_common.fake_llm import ScriptedChatModel, tool_call_message


def run(app, **payload):
    response = TestClient(app).post(
        "/v1/diagnose", json=payload, headers={"X-Correlation-ID": "corr-1"}
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def test_stream_reports_tool_calls_then_the_answer(config):
    llm = ScriptedChatModel(
        script=[
            tool_call_message("get_ppp_session"),
            AIMessage("<think>hmm</think>Findings: PPP down."),
        ]
    )
    events = run(
        create_app(config, llm),
        customer_id="C1002", message="no internet", session_id="s1",
    )

    assert [e.type for e in events] == [
        EventType.STATUS_UPDATE,
        EventType.TOOL_CALL,
        EventType.TOOL_CALL,
        EventType.AGENT_MESSAGE,
        EventType.DONE,
    ]
    started, completed = events[1].data, events[2].data
    assert started["status"] == "started" and started["tool"] == "get_ppp_session"
    assert completed["status"] == "completed" and completed["result"]["status"] == "down"
    assert events[3].data["text"] == "Findings: PPP down."
    assert {e.session_id for e in events} == {"s1"}
    assert {e.correlation_id for e in events} == {"corr-1"}
    assert {e.source for e in events} == {"inhome-agent"}


def test_history_is_passed_to_the_model(config):
    llm = ScriptedChatModel(script=[AIMessage("ok")])
    run(
        create_app(config, llm),
        customer_id="C1002", message="and now?",
        history=[
            {"role": "user", "content": "no internet"},
            {"role": "assistant", "content": "PPP is down"},
        ],
    )
    sent = llm.received[0][1:]
    assert [m.content for m in sent] == ["no internet", "PPP is down", "and now?"]
    assert isinstance(sent[0], HumanMessage) and isinstance(sent[1], AIMessage)


def test_model_failure_is_reported_as_error_then_done(config):
    llm = ScriptedChatModel(script=[])
    events = run(create_app(config, llm), customer_id="C1002", message="x")
    assert [e.type for e in events][-2:] == [EventType.ERROR, EventType.DONE]


def test_unreachable_mcp_server_is_reported_as_error():
    bad = Config(diagnostics_mcp_url="http://127.0.0.1:9/mcp")
    events = run(create_app(bad, ScriptedChatModel(script=[])), customer_id="C1002", message="x")
    assert [e.type for e in events] == [EventType.ERROR, EventType.DONE]


def test_endless_tool_loop_is_stopped(config):
    llm = ScriptedChatModel(
        script=[tool_call_message("get_ppp_session", call_id=f"c{i}") for i in range(30)]
    )
    events = run(create_app(config, llm), customer_id="C1002", message="loop")
    assert events[-2].type is EventType.ERROR and "in time" in events[-2].data["message"]


def test_health(config):
    assert TestClient(create_app(config, ScriptedChatModel(script=[]))).get("/health").json() == {
        "status": "ok", "agent": "inhome-agent",
    }


@pytest.mark.llm
@pytest.mark.skipif(os.getenv("RUN_LLM_TESTS") != "1", reason="needs Ollama")
def test_real_model_diagnoses_the_stuck_modem(mcp_url):
    """Run with RUN_LLM_TESTS=1 once Ollama is up (docker compose up -d)."""
    config = Config(diagnostics_mcp_url=mcp_url)
    events = run(create_app(config), customer_id="C1002", message="Customer has no internet.")
    called = [e.data["tool"] for e in events if e.type is EventType.TOOL_CALL]
    assert "get_ppp_session" in called
    answer = next(e for e in events if e.type is EventType.AGENT_MESSAGE).data["text"]
    assert answer.strip()
