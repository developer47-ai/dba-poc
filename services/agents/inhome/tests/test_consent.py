"""Human-in-the-loop: the expert's decision on an approval request, end to end.

Real MCP servers and simulator; only the LLM is scripted. The first turn stops at the approval
step; the second turn carries the expert's decision.
"""

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from inhome_agent import create_app
from inhome_agent.workflow import REJECTED_ANSWER
from poc_common.events import EventType, parse_sse
from poc_common.fake_llm import ScriptedChatModel, tool_call_message

STUCK = "The customer has no internet and the modem's internet light is orange"
WIFI = "The customer's laptop cannot connect to the wifi"


def choice(outcome: str) -> AIMessage:
    return tool_call_message("report_outcome", {"outcome": outcome, "reason": "scripted"})


def run(config, script, *, customer_id, message, state=None, consent=None):
    llm = ScriptedChatModel(script=script)
    payload = {"customer_id": customer_id, "message": message, "session_id": "s1"}
    if state is not None:
        payload["state"] = state
    if consent is not None:
        payload["consent"] = consent
    response = TestClient(create_app(config, llm)).post("/v1/diagnose", json=payload)
    events = parse_sse(response.text)
    assert events[-1].type is EventType.DONE
    assert not [e for e in events if e.type is EventType.ERROR], events
    return events, llm


def steps(events, name):
    return [e.data for e in events if e.type is EventType.STATUS_UPDATE and e.data["step"] == name]


def tool_calls(events):
    return [(e.data["tool"], e.data["status"]) for e in events if e.type is EventType.TOOL_CALL]


def saved_state(events):
    return next(e.data["state"] for e in events if e.type is EventType.AGENT_STATE)


def ask_for_approval(config, script, *, customer_id, message):
    """Turn 1: run until the agent asks for approval; returns (consent request, saved state)."""
    events, _ = run(config, script, customer_id=customer_id, message=message)
    request = next(e.data for e in events if e.type is EventType.CONSENT_REQUEST)
    return request, saved_state(events)


def test_approved_reboot_is_executed_and_the_sop_finishes_resolved(config):
    """Scenario 1 - C1002 stuck modem: approve -> reboot -> PPP up -> resolved."""
    request, state = ask_for_approval(
        config, [choice("troubleshoot"), choice("down"), choice("modem_stuck"),
                 AIMessage("Please approve the reboot.")],
        customer_id="C1002", message=STUCK)
    assert request["action"] == "reboot_device"
    assert state["sop"]["action"] == "reboot_device"
    assert state["sop"]["arguments"] == {"customer_id": "C1002"}

    events, llm = run(
        config, [choice("up"), AIMessage("Solved [SOP-IH-001 v1.0 › S5].")],
        customer_id="C1002", message="Approved: reboot_device", state=state,
        consent={"consent_id": request["consent_id"], "approved": True},
    )

    resolved = steps(events, "consent_resolved")[0]
    assert resolved["approved"] is True and resolved["action"] == "reboot_device"
    assert steps(events, "intent") == [], "an approval is not a new request"
    calls = [e.data for e in events if e.type is EventType.TOOL_CALL]
    assert [(c["tool"], c["status"]) for c in calls] == [
        ("reboot_device", "started"), ("reboot_device", "completed"),
        ("get_ppp_session", "started"), ("get_ppp_session", "completed")]
    assert calls[0]["arguments"] == {"customer_id": "C1002"}
    assert calls[1]["result"]["accepted"] is True
    assert calls[3]["result"]["status"] == "up", "the simulated modem really was rebooted"
    assert [s["step_id"] for s in steps(events, "sop_step")] == ["S4", "S5"]
    assert [(o["step_id"], o["outcome"]) for o in steps(events, "sop_outcome")] == [
        ("S3", "done"), ("S4", "up")]
    assert '"status":"up"' in llm.received[0][1].content
    assert "PROCEDURE ENDS (resolved)" in llm.received[1][1].content
    assert "reboot_device returned" in llm.received[1][1].content
    assert saved_state(events) == {"sop": None}


def test_approved_wifi_reset_fixes_the_wifi_scenario(config):
    """Scenario 2 - C1003 changed Wi-Fi: approve -> reset -> devices reconnect -> resolved."""
    request, state = ask_for_approval(
        config, [choice("troubleshoot"), choice("differs_from_sticker"), AIMessage("Approve?")],
        customer_id="C1003", message=WIFI)
    assert request["action"] == "reset_wifi" and request["sop_id"] == "SOP-IH-002"

    events, _ = run(
        config, [choice("connected"), AIMessage("Solved [SOP-IH-002 v1.0 › S5].")],
        customer_id="C1003", message="Approved: reset_wifi", state=state,
        consent={"consent_id": request["consent_id"], "approved": True},
    )

    assert [t for t in tool_calls(events) if t[1] == "completed"] == [
        ("reset_wifi", "completed"), ("get_managed_devices", "completed")]
    devices = [e.data["result"] for e in events if e.type is EventType.TOOL_CALL
               and e.data["tool"] == "get_managed_devices" and e.data["status"] == "completed"][0]
    assert devices and all(d["connected"] for d in devices)
    assert [s["step_id"] for s in steps(events, "sop_step")] == ["S3", "S5"]
    assert saved_state(events) == {"sop": None}


def test_declined_reboot_changes_nothing_and_takes_the_decline_path(config):
    request, state = ask_for_approval(
        config, [choice("troubleshoot"), choice("down"), choice("modem_stuck"),
                 AIMessage("Please approve the reboot.")],
        customer_id="C1002", message=STUCK)

    events, llm = run(
        config, [AIMessage("Reboot declined; escalating.")],
        customer_id="C1002", message="Declined: reboot_device", state=state,
        consent={"consent_id": request["consent_id"], "approved": False,
                 "comment": "customer is on a call"},
    )

    assert steps(events, "consent_resolved")[0]["approved"] is False
    assert not [t for t in tool_calls(events) if t[0] == "reboot_device"], "nothing was changed"
    outcome = steps(events, "sop_outcome")[0]
    assert outcome["outcome"] == "declined" and "customer is on a call" in outcome["reason"]
    # SOP-IH-001 S10 escalates to SOP-ESC-001, whose incident ticket is not part of this demo.
    assert [s["doc_id"] for s in steps(events, "sop_selected")] == ["SOP-ESC-001"]
    prompt = llm.received[0][1].content
    assert "CANNOT CONTINUE" in prompt and "not part of this demo" in prompt
    assert saved_state(events) == {"sop": None}


def test_decision_for_another_request_is_refused_and_changes_nothing(config):
    request, state = ask_for_approval(
        config, [choice("troubleshoot"), choice("down"), choice("modem_stuck"),
                 AIMessage("Please approve the reboot.")],
        customer_id="C1002", message=STUCK)

    events, llm = run(config, [], customer_id="C1002", message="Approved: reboot_device",
                      state=state, consent={"consent_id": "someone-elses", "approved": True})

    assert steps(events, "consent_rejected")[0]["consent_id"] == "someone-elses"
    assert tool_calls(events) == [] and llm.received == []
    answer = next(e.data["text"] for e in events
                  if e.type is EventType.AGENT_MESSAGE and e.data["final"])
    assert answer == REJECTED_ANSWER
    assert saved_state(events) == state, "the original request stays pending"
    assert request["consent_id"] == state["sop"]["consent_id"]


def test_decision_without_a_pending_request_is_refused(config):
    events, _ = run(config, [], customer_id="C1002", message="Approved: reboot_device",
                    consent={"consent_id": "c1", "approved": True})
    assert steps(events, "consent_rejected") and tool_calls(events) == []


def test_saved_request_for_another_customer_is_refused(config):
    forged = {"sop": {"doc_id": "SOP-IH-001", "version": "1.0", "step_id": "S3",
                      "awaiting": "consent", "consent_id": "c1", "action": "reboot_device",
                      "arguments": {"customer_id": "C1006"}}}
    events, _ = run(config, [], customer_id="C1002", message="Approved: reboot_device",
                    state=forged, consent={"consent_id": "c1", "approved": True})
    assert steps(events, "consent_rejected") and tool_calls(events) == []


def test_saved_action_must_match_the_procedure_step(config):
    forged = {"sop": {"doc_id": "SOP-IH-001", "version": "1.0", "step_id": "S3",
                      "awaiting": "consent", "consent_id": "c1", "action": "factory_reset_device",
                      "arguments": {"customer_id": "C1002"}}}
    events, _ = run(config, [], customer_id="C1002", message="Approved: factory_reset_device",
                    state=forged, consent={"consent_id": "c1", "approved": True})
    assert steps(events, "consent_rejected") and tool_calls(events) == []
