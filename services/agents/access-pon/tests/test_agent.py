"""The Access-PON agent: a case handed over from the In-Home agent, and the booking approval.

Real MCP servers and simulator; only the LLM is scripted. C1001 is a GPON customer whose OLT
port is down, so SOP-PON-001 ends in booking an engineer, which needs the expert's approval.
"""

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from access_pon_agent import SPEC, create_app
from poc_common.events import EventType, parse_sse
from poc_common.fake_llm import ScriptedChatModel, tool_call_message

HANDED_OVER = (
    "Handed over from inhome-agent (SOP-IH-001 v1.0 › S9). Findings so far: the modem is "
    "fine, the ONT shows no light. Expert's request: no internet at all"
)


def choice(outcome: str) -> AIMessage:
    return tool_call_message("report_outcome", {"outcome": outcome, "reason": "scripted"})


def run(config, script, *, message, state=None, consent=None):
    llm = ScriptedChatModel(script=script)
    payload = {"customer_id": "C1001", "message": message, "session_id": "s1"}
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


def saved_state(events):
    return next(e.data["state"] for e in events if e.type is EventType.AGENT_STATE)


def ask_for_booking(config):
    events, _ = run(
        config,
        [choice("troubleshoot"), choice("olt_port_down"), choice("slots_found"),
         AIMessage("Please approve the proposed slot.")],
        message=HANDED_OVER,
    )
    request = next(e.data for e in events if e.type is EventType.CONSENT_REQUEST)
    return events, request, saved_state(events)


def test_agent_identity():
    assert SPEC.agent_id == "access-pon" and SPEC.source == "access-pon-agent"


def test_handed_over_case_follows_the_pon_sop_and_stops_at_the_booking_approval(config):
    events, request, state = ask_for_booking(config)

    assert steps(events, "sop_selected")[0]["doc_id"] == "SOP-PON-001"
    assert [s["step_id"] for s in steps(events, "sop_step")] == ["S1", "S5", "S6"]
    tools = [e.data["tool"] for e in events if e.type is EventType.TOOL_CALL]
    assert tools == ["get_ont_status", "get_ont_status",
                     "search_appointment_slots", "search_appointment_slots"]
    assert request["action"] == "book_appointment"
    assert request["sop_id"] == "SOP-PON-001" and request["step_id"] == "S6"
    assert request["arguments"]["customer_id"] == "C1001"
    assert request["arguments"]["slot_id"]
    assert state["sop"]["awaiting"] == "consent"


def test_nothing_is_booked_before_the_expert_approves(config, scheduling_url):
    ask_for_booking(config)
    records = httpx.get(f"{scheduling_url}/v1/records", params={"customer_id": "C1001"}).json()
    assert records["appointments"] == []


def test_approved_booking_is_made_and_the_sop_finishes_resolved(config, scheduling_url):
    _, request, state = ask_for_booking(config)

    events, _ = run(
        config, [AIMessage("Engineer booked [SOP-PON-001 v1.0 › S8].")],
        message="Approved: book_appointment", state=state,
        consent={"consent_id": request["consent_id"], "approved": True},
    )

    assert steps(events, "consent_resolved")[0]["approved"] is True
    booked = httpx.get(f"{scheduling_url}/v1/records", params={"customer_id": "C1001"}).json()
    booked_slots = [a["slot"]["slot_id"] for a in booked["appointments"]]
    assert booked_slots == [request["arguments"]["slot_id"]]
    assert saved_state(events)["sop"] is None


def test_declined_booking_books_nothing_and_escalates(config, scheduling_url):
    _, request, state = ask_for_booking(config)

    events, _ = run(
        config, [AIMessage("No booking made; escalate [SOP-PON-001 v1.0 › S9].")],
        message="Declined: book_appointment", state=state,
        consent={"consent_id": request["consent_id"], "approved": False},
    )

    assert steps(events, "consent_resolved")[0]["approved"] is False
    records = httpx.get(f"{scheduling_url}/v1/records", params={"customer_id": "C1001"}).json()
    assert records["appointments"] == []