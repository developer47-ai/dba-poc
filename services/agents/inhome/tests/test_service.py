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


def test_hybrid_qwen3_gets_the_no_think_switch(mcp_url):
    config = Config(diagnostics_mcp_url=mcp_url, chat_model="qwen3:8b")
    llm = ScriptedChatModel(script=[AIMessage("ok")])
    run(create_app(config, llm), customer_id="C1002", message="hi")
    assert llm.received[0][0].content.endswith("\n/no_think")


def test_default_instruct_model_gets_the_plain_prompt(config):
    llm = ScriptedChatModel(script=[AIMessage("ok")])
    run(create_app(config, llm), customer_id="C1002", message="hi")
    assert "/no_think" not in llm.received[0][0].content


def test_thinking_can_be_enabled_and_reports_progress(mcp_url):
    config = Config(diagnostics_mcp_url=mcp_url, llm_thinking=True)
    hidden = " ".join(["step"] * 60)
    llm = ScriptedChatModel(script=[AIMessage(f"<think>{hidden}</think>PPP is down.")])
    events = run(create_app(config, llm), customer_id="C1002", message="hi")

    assert not llm.received[0][0].content.endswith("/no_think")
    progress = [e.data["tokens"] for e in events if e.data.get("step") == "thinking"]
    assert progress == [25, 50]
    deltas = "".join(e.data["text"] for e in events if e.type is EventType.AGENT_MESSAGE_DELTA)
    assert deltas == "PPP is down."


def test_stream_reports_tool_calls_then_streams_the_answer(config):
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

    types = [e.type for e in events]
    assert [t for t in types if t is not EventType.AGENT_MESSAGE_DELTA] == [
        EventType.STATUS_UPDATE,   # diagnosis started
        EventType.STATUS_UPDATE,   # reasoning, round 1
        EventType.TOOL_CALL,
        EventType.TOOL_CALL,
        EventType.STATUS_UPDATE,   # reasoning, round 2 (on the tool results)
        EventType.AGENT_MESSAGE,
        EventType.DONE,
    ]
    assert [e.data.get("round") for e in events if e.data.get("step") == "reasoning"] == [1, 2]
    started, completed = events[2].data, events[3].data
    assert started["status"] == "started" and started["tool"] == "get_ppp_session"
    assert completed["status"] == "completed" and completed["result"]["status"] == "down"

    deltas = [e for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    final = next(e for e in events if e.type is EventType.AGENT_MESSAGE)
    assert len(deltas) > 1, "answer must arrive in several pieces, not as one batch"
    assert types.index(EventType.AGENT_MESSAGE_DELTA) < types.index(EventType.AGENT_MESSAGE)
    assert "".join(d.data["text"] for d in deltas) == "Findings: PPP down."
    assert final.data == {
        "message_id": deltas[0].data["message_id"], "text": "Findings: PPP down.", "final": True,
    }
    assert {d.data["message_id"] for d in deltas} == {final.data["message_id"]}
    assert {e.session_id for e in events} == {"s1"}
    assert {e.correlation_id for e in events} == {"corr-1"}
    assert {e.source for e in events} == {"inhome-agent"}


def test_text_before_tool_calls_is_streamed_but_not_final(config):
    narration = AIMessage(
        "Let me check the line.",
        tool_calls=[{"name": "get_ppp_session", "args": {}, "id": "n1"}],
    )
    llm = ScriptedChatModel(script=[narration, AIMessage("PPP is down.")])
    events = run(create_app(config, llm), customer_id="C1002", message="no internet")

    messages = [e.data for e in events if e.type is EventType.AGENT_MESSAGE]
    assert [(m["text"], m["final"]) for m in messages] == [
        ("Let me check the line.", False), ("PPP is down.", True),
    ]
    assert messages[0]["message_id"] != messages[1]["message_id"]


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
    final = [e for e in events if e.type is EventType.AGENT_MESSAGE and e.data["final"]]
    assert final and final[-1].data["text"].strip()
    deltas = [e for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    assert len(deltas) > 1, "the real model should stream the answer token by token"
