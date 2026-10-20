from chat_ui.view import TurnView, apply_event, summarize
from poc_common.events import Event, EventType


def ev(type_, source="inhome-agent", **data):
    return Event(type=type_, source=source, data=data)


def play(*events):
    view, lines = TurnView(), []
    for event in events:
        line = apply_event(view, event)
        if line:
            lines.append(line)
    return view, lines


def test_full_turn_builds_activity_log_tools_and_answer():
    view, lines = play(
        ev(EventType.STATUS_UPDATE, "orchestrator", step="routing", agent="inhome-agent",
           reason="start with in-home diagnosis"),
        ev(EventType.AGENT_HANDOFF, "orchestrator", from_agent="orchestrator",
           to_agent="inhome-agent"),
        ev(EventType.STATUS_UPDATE, step="diagnosis_started", tools=9),
        ev(EventType.TOOL_CALL, tool="get_ppp_session", arguments={}, status="started"),
        ev(EventType.TOOL_CALL, tool="get_ppp_session", status="completed",
           result={"status": "down"}),
        ev(EventType.AGENT_MESSAGE_DELTA, message_id="m1", text="PPP is "),
        ev(EventType.AGENT_MESSAGE_DELTA, message_id="m1", text="down."),
        ev(EventType.AGENT_MESSAGE, message_id="m1", text="PPP is down.", final=True),
        ev(EventType.DONE, "orchestrator"),
    )
    assert view.agent == "inhome-agent"
    assert [(t.name, t.status, t.result) for t in view.tools] == [
        ("get_ppp_session", "done", {"status": "down"}),
    ]
    assert view.text == "PPP is down." and view.final and view.done and view.error is None
    assert view.log == lines
    assert lines[0].startswith("🧭 Routed to **inhome-agent**")
    assert "✅ `get_ppp_session` → status=down" in lines
    assert view.summary_label == "inhome-agent · 1 tool call(s)"


def test_text_grows_token_by_token_while_streaming():
    view = TurnView()
    apply_event(view, ev(EventType.AGENT_MESSAGE_DELTA, message_id="m1", text="The "))
    assert view.text == "The " and not view.final
    apply_event(view, ev(EventType.AGENT_MESSAGE_DELTA, message_id="m1", text="modem"))
    assert view.text == "The modem"
    assert view.status_label == "Writing the answer…"


def test_narration_before_tool_calls_moves_to_the_log():
    view, lines = play(
        ev(EventType.AGENT_MESSAGE_DELTA, message_id="n1", text="Let me check."),
        ev(EventType.AGENT_MESSAGE, message_id="n1", text="Let me check.", final=False),
        ev(EventType.AGENT_MESSAGE_DELTA, message_id="m2", text="All good."),
    )
    assert "💭 Let me check." in lines
    assert view.text == "All good."


def test_reasoning_and_thinking_progress_update_the_label_only():
    view, lines = play(
        ev(EventType.STATUS_UPDATE, step="reasoning", round=2),
    )
    assert view.status_label == "🧠 inhome-agent is reasoning (step 2)…" and lines == []
    apply_event(view, ev(EventType.STATUS_UPDATE, step="thinking", tokens=50))
    assert view.status_label == "🧠 Thinking… (50 tokens)" and view.log == []
    apply_event(view, ev(EventType.STATUS_UPDATE, step="reasoning", purpose="outcome",
                         step_id="S2"))
    assert view.status_label == "🧠 Interpreting step S2…"
    apply_event(view, ev(EventType.STATUS_UPDATE, step="reasoning", purpose="answer"))
    assert view.status_label == "🧠 Writing the answer…" and view.log == []


def test_sop_progress_citations_and_approval_are_shown():
    view, lines = play(
        ev(EventType.STATUS_UPDATE, step="intent", intent="troubleshoot"),
        ev(EventType.STATUS_UPDATE, step="sop_selected", doc_id="SOP-IH-001", version="1.0",
           title="No internet while the modem is online"),
        ev(EventType.KNOWLEDGE_ARTICLE, doc_id="SOP-IH-001", citation="SOP-IH-001 v1.0",
           title="No internet while the modem is online"),
        ev(EventType.STATUS_UPDATE, step="sop_step", doc_id="SOP-IH-001", step_id="S2",
           title="Check the indicator lights", actor="agent"),
        ev(EventType.STATUS_UPDATE, step="sop_outcome", doc_id="SOP-IH-001", step_id="S2",
           outcome="modem_stuck", reason="wan green, internet orange", source="agent"),
        ev(EventType.CONSENT_REQUEST, action="reboot_device", sop_id="SOP-IH-001",
           step_id="S3", risk="medium", description="Reboot the modem", consent_id="c1"),
        ev(EventType.DONE, "orchestrator"),
    )
    assert lines == [
        "🗂️ Request type: troubleshooting",
        "📘 Following **SOP-IH-001** v1.0 — No internet while the modem is online",
        "📎 SOP-IH-001 v1.0 — No internet while the modem is online",
        "▶ **S2** — Check the indicator lights (agent)",
        "↳ S2: **modem_stuck** — wan green, internet orange",
        "🛑 Approval needed: `reboot_device` (SOP-IH-001 › S3)",
    ]
    assert view.citations == ["SOP-IH-001 v1.0"]
    assert view.consent["action"] == "reboot_device"
    assert view.summary_label == "agent · SOP-IH-001 · 0 tool call(s)"


def test_exploratory_fallback_is_labelled():
    _, lines = play(ev(EventType.STATUS_UPDATE, step="no_sop"),
                    ev(EventType.STATUS_UPDATE, step="exploratory_started", tools=9))
    assert lines == ["⚠️ No approved procedure matches — exploratory check",
                     "🔎 Exploratory check (9 read-only tools)"]


def test_failed_tool_and_error_are_visible():
    view, lines = play(
        ev(EventType.TOOL_CALL, tool="get_ont_status", arguments={}, status="started"),
        ev(EventType.TOOL_CALL, tool="get_ont_status", status="completed",
           result="Tool error: no ONT on this connection"),
        ev(EventType.ERROR, "orchestrator", message="The inhome-agent is not available"),
        ev(EventType.DONE, "orchestrator"),
    )
    assert view.tools[0].status == "failed"
    assert lines[1].startswith("⚠️ `get_ont_status`")
    assert view.error == "The inhome-agent is not available"
    assert view.summary_label == "Something went wrong"


def test_summarize_keeps_results_short():
    assert summarize({"status": "up", "leds": {"wan": "green"}}) == "status=up"
    assert summarize([1, 2, 3]) == "3 item(s)"
    assert summarize("x" * 500).endswith("…") and len(summarize("x" * 500)) == 140
