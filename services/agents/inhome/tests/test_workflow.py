"""SOP-guided troubleshooting, scenario by scenario (scripted LLM, real MCP servers)."""

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from inhome_agent import create_app
from poc_common.events import EventType, parse_sse
from poc_common.fake_llm import ScriptedChatModel, tool_call_message

STUCK = "The customer has no internet and the modem's internet light is orange"
ANSWER = "Findings: PPP down [SOP-IH-001 › S1]. Next step: approve the reboot [SOP-IH-001 › S3]."


def choice(outcome: str, reason: str = "scripted") -> AIMessage:
    return tool_call_message("report_outcome", {"outcome": outcome, "reason": reason})


def run(config, script, *, customer_id="C1002", message="no internet", state=None):
    llm = ScriptedChatModel(script=script)
    payload = {"customer_id": customer_id, "message": message, "session_id": "s1"}
    if state is not None:
        payload["state"] = state
    response = TestClient(create_app(config, llm)).post("/v1/diagnose", json=payload)
    events = parse_sse(response.text)
    assert events[-1].type is EventType.DONE
    assert not [e for e in events if e.type is EventType.ERROR], events
    return events, llm


def statuses(events, step):
    return [e.data for e in events if e.type is EventType.STATUS_UPDATE and e.data["step"] == step]


def saved_state(events):
    return next(e.data["state"] for e in events if e.type is EventType.AGENT_STATE)


def answer(events):
    return next(e.data["text"] for e in events
                if e.type is EventType.AGENT_MESSAGE and e.data["final"])


def test_stuck_modem_follows_the_sop_to_the_reboot_approval(config):
    """C1002: PPP down, internet light orange -> SOP-IH-001 S1, S2, then reboot needs approval."""
    events, llm = run(config, [
        choice("troubleshoot"), choice("down", "PPP status is down"),
        choice("modem_stuck", "wan green, internet orange"), AIMessage(ANSWER),
    ], message="The customer has no internet and the modem's internet light is orange")

    assert statuses(events, "intent")[0]["intent"] == "troubleshoot"
    assert statuses(events, "sop_selected")[0]["doc_id"] == "SOP-IH-001"
    assert [s["step_id"] for s in statuses(events, "sop_step")] == ["S1", "S2", "S3"]
    assert [(o["step_id"], o["outcome"]) for o in statuses(events, "sop_outcome")] == [
        ("S1", "down"), ("S2", "modem_stuck")]
    tools = [(e.data["tool"], e.data["status"]) for e in events if e.type is EventType.TOOL_CALL]
    assert tools == [("get_ppp_session", "started"), ("get_ppp_session", "completed"),
                     ("get_led_status", "started"), ("get_led_status", "completed")]
    consent = next(e.data for e in events if e.type is EventType.CONSENT_REQUEST)
    assert consent["action"] == "reboot_device" and consent["sop_id"] == "SOP-IH-001"
    assert consent["step_id"] == "S3" and consent["arguments"] == {"customer_id": "C1002"}
    citations = [e.data["citation"] for e in events if e.type is EventType.KNOWLEDGE_ARTICLE]
    assert citations == ["SOP-IH-001 v1.0"]

    # The LLM judged the real LED data, and the answer prompt says the reboot is NOT done.
    assert '"internet":"orange"' in llm.received[2][1].content
    final_prompt = llm.received[3][1].content
    assert "APPROVAL NEEDED [SOP-IH-001 v1.0 › S3]" in final_prompt
    assert "[SOP-IH-001 v1.0 › S2] Check the indicator lights" in final_prompt
    assert answer(events) == ANSWER
    deltas = [e for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    assert len(deltas) > 1 and "".join(d.data["text"] for d in deltas) == ANSWER
    assert saved_state(events) == {"sop": {
        "doc_id": "SOP-IH-001", "version": "1.0", "step_id": "S3", "awaiting": "consent",
        "consent_id": consent["consent_id"]}}


def test_question_for_the_customer_waits_and_the_reply_continues_the_sop(config):
    """C1006: Wi-Fi matches the sticker, devices offline -> customer must reconnect (S7)."""
    events, _ = run(config, [
        choice("troubleshoot"), choice("matches_defaults"), choice("disconnected"),
        AIMessage("Ask the customer to reconnect [SOP-IH-002 v1.0 › S7]."),
    ], customer_id="C1006", message="The customer's laptop cannot connect to the wifi")

    assert statuses(events, "sop_selected")[0]["doc_id"] == "SOP-IH-002"
    assert [s["step_id"] for s in statuses(events, "sop_step")] == ["S1", "S4", "S7"]
    assert not [e for e in events if e.type is EventType.CONSENT_REQUEST]
    state = saved_state(events)
    assert state["sop"]["step_id"] == "S7" and state["sop"]["awaiting"] == "expert"

    events, llm = run(config, [
        choice("fixed", "laptop connects again"), AIMessage("Solved [SOP-IH-002 v1.0 › S5]."),
    ], customer_id="C1006", message="Done, the laptop is online again", state=state)

    assert "Their reply: \"Done, the laptop is online again\"" in llm.received[0][1].content
    assert statuses(events, "intent") == []
    assert statuses(events, "sop_outcome")[0] == {
        "step": "sop_outcome", "doc_id": "SOP-IH-002", "step_id": "S7", "outcome": "fixed",
        "reason": "laptop connects again", "source": "expert"}
    assert [s["step_id"] for s in statuses(events, "sop_step")] == ["S5"]
    assert "PROCEDURE ENDS (resolved)" in llm.received[1][1].content
    assert saved_state(events) == {"sop": None}


def test_unclear_result_asks_the_expert_instead_of_guessing(config):
    events, llm = run(config, [
        choice("troubleshoot"), choice("unclear", "cannot tell"), AIMessage("Please confirm."),
    ], message="The customer has no internet and the modem's internet light is orange")

    assert statuses(events, "sop_outcome")[0]["outcome"] is None
    assert "UNCLEAR [SOP-IH-001 v1.0 › S1]" in llm.received[2][1].content
    assert saved_state(events)["sop"] == {
        "doc_id": "SOP-IH-001", "version": "1.0", "step_id": "S1", "awaiting": "expert"}


def test_faulty_modem_continues_with_the_replacement_procedure(config):
    """C1004: power light orange -> SOP-IH-001 S6 continues with SOP-IH-003 (factory reset)."""
    events, _ = run(config, [
        choice("troubleshoot"), choice("down"), choice("modem_fault"), AIMessage("Swap."),
    ], customer_id="C1004", message=STUCK)

    assert [s["doc_id"] for s in statuses(events, "sop_selected")] == ["SOP-IH-001", "SOP-IH-003"]
    assert statuses(events, "sop_selected")[1]["via"] == "SOP-IH-001 v1.0 › S6"
    assert [(s["doc_id"], s["step_id"]) for s in statuses(events, "sop_step")] == [
        ("SOP-IH-001", "S1"), ("SOP-IH-001", "S2"), ("SOP-IH-001", "S6"),
        ("SOP-IH-003", "S1"), ("SOP-IH-003", "S2")]
    consent = next(e.data for e in events if e.type is EventType.CONSENT_REQUEST)
    assert consent["action"] == "factory_reset_device" and consent["risk"] == "high"


def test_problem_outside_the_home_ends_with_a_handover(config):
    """C1001: LOS red -> SOP-IH-001 S9 hands over to the access network."""
    events, llm = run(config, [
        choice("troubleshoot"), choice("down"), choice("access_problem"), AIMessage("Handover."),
    ], customer_id="C1001", message=STUCK)

    assert [s["step_id"] for s in statuses(events, "sop_step")] == ["S1", "S2", "S9"]
    assert "PROCEDURE ENDS (handover → access-network)" in llm.received[3][1].content
    assert saved_state(events) == {"sop": None}


def test_knowledge_question_is_answered_from_cited_passages(config):
    events, llm = run(config, [
        choice("question"), AIMessage("Orange power means a hardware fault [KB-LED-001 v1.0]."),
    ], message="What does an orange power light on the modem mean?")

    assert statuses(events, "sop_selected") == []
    citations = [e.data["citation"] for e in events if e.type is EventType.KNOWLEDGE_ARTICLE]
    assert "KB-LED-001 v1.0" in citations
    assert "Passages:" in llm.received[1][1].content
    assert answer(events).startswith("Orange power means")


def test_question_outside_the_knowledge_base_needs_no_second_llm_call(config):
    events, llm = run(config, [choice("question")], message="How do I bake sourdough bread?")
    assert len(llm.received) == 1
    assert "does not cover this question" in answer(events)
    assert [e for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]


def test_pending_approval_does_not_capture_the_next_message(config):
    pending = {"sop": {"doc_id": "SOP-IH-001", "version": "1.0", "step_id": "S3",
                       "awaiting": "consent", "consent_id": "c1"}}
    events, _ = run(config, [choice("question"), AIMessage("Hardware fault [KB-LED-001 v1.0].")],
                    message="What does an orange power light mean?", state=pending)
    assert statuses(events, "intent")[0]["intent"] == "question"
    assert saved_state(events) == {"sop": None}


def test_reply_about_something_else_starts_a_new_request(config):
    waiting = {"sop": {"doc_id": "SOP-IH-002", "version": "1.0", "step_id": "S7",
                       "awaiting": "expert"}}
    events, _ = run(config, [
        choice("unclear"), choice("question"), AIMessage("Hardware fault [KB-LED-001 v1.0]."),
    ], message="What does an orange power light mean?", state=waiting)
    assert statuses(events, "sop_resume_skipped")[0]["step_id"] == "S7"
    assert statuses(events, "intent")[0]["intent"] == "question"
