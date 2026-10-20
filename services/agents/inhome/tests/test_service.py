import os

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from inhome_agent import create_app
from poc_common.config import Config
from poc_common.events import EventType, parse_sse
from poc_common.fake_llm import ScriptedChatModel, tool_call_message

NO_SOP = "The smart TV remote stopped responding"


def choice(outcome: str) -> AIMessage:
    return tool_call_message("report_outcome", {"outcome": outcome, "reason": "scripted"})


def run(app, **payload):
    response = TestClient(app).post(
        "/v1/diagnose", json=payload, headers={"X-Correlation-ID": "corr-1"}
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def test_hybrid_qwen3_gets_the_no_think_switch(config):
    hybrid = config.model_copy(update={"chat_model": "qwen3:8b"})
    llm = ScriptedChatModel(script=[choice("question")])
    run(create_app(hybrid, llm), customer_id="C1002", message="How do I bake bread?")
    assert llm.received[0][0].content.endswith("\n/no_think")


def test_default_instruct_model_gets_the_plain_prompt(config):
    llm = ScriptedChatModel(script=[choice("question")])
    run(create_app(config, llm), customer_id="C1002", message="How do I bake bread?")
    assert "/no_think" not in llm.received[0][0].content


def test_thinking_can_be_enabled_and_reports_progress(config):
    thinking = config.model_copy(update={"llm_thinking": True})
    hidden = " ".join(["step"] * 60)
    llm = ScriptedChatModel(script=[
        choice("question"), AIMessage(f"<think>{hidden}</think>Hardware fault."),
    ])
    events = run(create_app(thinking, llm), customer_id="C1002",
                 message="What does an orange power light mean?")

    progress = [e.data["tokens"] for e in events if e.data.get("step") == "thinking"]
    assert progress == [25, 50]
    deltas = "".join(e.data["text"] for e in events if e.type is EventType.AGENT_MESSAGE_DELTA)
    assert deltas == "Hardware fault."


def test_without_a_matching_sop_the_agent_does_an_exploratory_check(config):
    llm = ScriptedChatModel(script=[
        choice("troubleshoot"),
        tool_call_message("get_ppp_session"),
        AIMessage("<think>hmm</think>No approved procedure covers this. PPP down."),
    ])
    events = run(create_app(config, llm), customer_id="C1002", message=NO_SOP, session_id="s1")

    steps = [e.data.get("step") for e in events if e.type is EventType.STATUS_UPDATE]
    assert steps == ["diagnosis_started", "reasoning", "intent", "no_sop",
                     "exploratory_started", "reasoning", "reasoning"]
    assert "No approved NetCo procedure" in llm.received[1][0].content
    tools = [e.data for e in events if e.type is EventType.TOOL_CALL]
    assert [(t["tool"], t["status"]) for t in tools] == [
        ("get_ppp_session", "started"), ("get_ppp_session", "completed")]
    assert tools[1]["result"]["status"] == "down"

    deltas = [e for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    final = next(e for e in events if e.type is EventType.AGENT_MESSAGE)
    assert len(deltas) > 1, "answer must arrive in several pieces, not as one batch"
    assert "".join(d.data["text"] for d in deltas) == final.data["text"]
    assert final.data["final"] is True
    assert [e.type for e in events][-2:] == [EventType.AGENT_STATE, EventType.DONE]
    assert {e.session_id for e in events} == {"s1"}
    assert {e.correlation_id for e in events} == {"corr-1"}
    assert {e.source for e in events} == {"inhome-agent"}


def test_exploratory_text_before_tool_calls_is_streamed_but_not_final(config):
    narration = AIMessage(
        "Let me check the line.",
        tool_calls=[{"name": "get_ppp_session", "args": {}, "id": "n1"}],
    )
    llm = ScriptedChatModel(script=[choice("troubleshoot"), narration, AIMessage("PPP is down.")])
    events = run(create_app(config, llm), customer_id="C1002", message=NO_SOP)

    messages = [e.data for e in events if e.type is EventType.AGENT_MESSAGE]
    assert [(m["text"], m["final"]) for m in messages] == [
        ("Let me check the line.", False), ("PPP is down.", True),
    ]


def test_history_is_passed_to_the_exploratory_model(config):
    llm = ScriptedChatModel(script=[choice("troubleshoot"), AIMessage("ok")])
    run(
        create_app(config, llm),
        customer_id="C1002", message=NO_SOP,
        history=[
            {"role": "user", "content": "no internet"},
            {"role": "assistant", "content": "PPP is down"},
        ],
    )
    sent = llm.received[1][1:]
    assert [m.content for m in sent] == ["no internet", "PPP is down", NO_SOP]
    assert isinstance(sent[0], HumanMessage) and isinstance(sent[1], AIMessage)


def test_model_failure_is_reported_as_error_then_done(config):
    events = run(create_app(config, ScriptedChatModel(script=[])), customer_id="C1002",
                 message="x")
    assert [e.type for e in events][-2:] == [EventType.ERROR, EventType.DONE]


def test_unreachable_knowledge_server_is_reported_as_error(config):
    bad = config.model_copy(update={"knowledge_mcp_url": "http://127.0.0.1:9/mcp"})
    llm = ScriptedChatModel(script=[choice("troubleshoot")])
    events = run(create_app(bad, llm), customer_id="C1002", message="no internet")
    assert [e.type for e in events][-2:] == [EventType.ERROR, EventType.DONE]
    assert "Diagnosis failed" in events[-2].data["message"]


def test_endless_exploratory_tool_loop_is_stopped(config):
    llm = ScriptedChatModel(script=[choice("troubleshoot")] + [
        tool_call_message("get_ppp_session", call_id=f"c{i}") for i in range(30)
    ])
    events = run(create_app(config, llm), customer_id="C1002", message=NO_SOP)
    assert events[-2].type is EventType.ERROR and "in time" in events[-2].data["message"]


def test_health(config):
    assert TestClient(create_app(config, ScriptedChatModel(script=[]))).get("/health").json() == {
        "status": "ok", "agent": "inhome-agent",
    }


@pytest.mark.llm
@pytest.mark.skipif(os.getenv("RUN_LLM_TESTS") != "1", reason="needs Ollama")
def test_real_model_follows_the_stuck_modem_sop(mcp_url, knowledge_url):
    """Run with RUN_LLM_TESTS=1 once Ollama is up (docker compose up -d)."""
    config = Config(diagnostics_mcp_url=mcp_url, knowledge_mcp_url=knowledge_url)
    events = run(create_app(config), customer_id="C1002",
                 message="The customer has no internet and the modem's internet light is orange.")
    selected = [e.data["doc_id"] for e in events if e.data.get("step") == "sop_selected"]
    assert selected[:1] == ["SOP-IH-001"]
    consent = [e.data for e in events if e.type is EventType.CONSENT_REQUEST]
    assert consent and consent[0]["action"] == "reboot_device"
    final = [e for e in events if e.type is EventType.AGENT_MESSAGE and e.data["final"]]
    assert final and final[-1].data["text"].strip()
    deltas = [e for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    assert len(deltas) > 1, "the real model should stream the answer token by token"
