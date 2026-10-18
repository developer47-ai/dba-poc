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
