"""Tracing: one In-Home turn leaves a trace that shows the procedure step by step."""

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from inhome_agent import create_app
from poc_common.fake_llm import ScriptedChatModel, tool_call_message
from poc_common.llm_trace import LlmSpanHandler


def choice(outcome: str) -> AIMessage:
    return tool_call_message("report_outcome", {"outcome": outcome, "reason": "scripted"})


def test_a_turn_is_traced_down_to_steps_llm_calls_and_tool_calls(spans, config):
    llm = ScriptedChatModel(
        script=[choice("troubleshoot"), choice("down"), choice("modem_stuck"),
                AIMessage("Please approve the reboot.")],
        callbacks=[LlmSpanHandler()],
    )
    client = TestClient(create_app(config, llm))

    response = client.post("/v1/diagnose", json={
        "customer_id": "C1002", "session_id": "s1",
        "message": "The customer has no internet and the modem's internet light is orange"})
    assert response.status_code == 200

    finished = spans.get_finished_spans()
    (request,) = [s for s in finished if s.name == "POST /v1/diagnose"]
    ours = [s for s in finished if s.name != "GET"]  # GET: the simulator, traced in-process
    assert {s.context.trace_id for s in ours} == {request.context.trace_id}
    names = [s.name for s in finished]
    assert {"sop.step S1", "sop.step S2", "sop.step S3"} <= set(names)
    steps = {s.name: s for s in finished if s.name.startswith("sop.step")}
    assert steps["sop.step S1"].attributes["sop.id"] == "SOP-IH-001"
    assert steps["sop.step S1"].attributes["sop.tool"] == "network-diagnostics.get_ppp_session"
    assert steps["sop.step S1"].attributes["sop.outcome"] == "down"
    assert steps["sop.step S2"].attributes["sop.outcome"] == "modem_stuck"

    compose = next(s for s in finished if s.name == "workflow.compose")
    assert compose.attributes["sop.stop"] == "consent"
    tools = [s.attributes["mcp.tool"] for s in finished if s.name == "mcp.call_tool"]
    assert {"get_ppp_session", "get_led_status", "find_troubleshooting_sop"} <= set(tools)
    assert "reboot_device" not in tools, "nothing runs before the expert approves"
    assert len([s for s in finished if s.name == "llm.chat"]) == 4
    assert request.parent is None, "the request span is the root of the agent's part"