from poc_common.events import (
    ConsentRequest,
    Event,
    EventType,
    consent_request_event,
    parse_sse,
)


def test_sse_frame_format():
    event = Event(type=EventType.AGENT_MESSAGE, source="inhome-agent", data={"text": "hi"})
    frame = event.to_sse()
    assert frame.startswith(f"id: {event.event_id}\nevent: agent_message\ndata: {{")
    assert frame.endswith("\n\n")


def test_sse_roundtrip_preserves_events():
    first = Event(type=EventType.STATUS_UPDATE, session_id="s1", data={"step": "scan"})
    second = Event(type=EventType.DONE, session_id="s1")
    parsed = parse_sse(first.to_sse() + ": heartbeat\n\n" + second.to_sse())
    assert [e.type for e in parsed] == [EventType.STATUS_UPDATE, EventType.DONE]
    assert parsed[0].data == {"step": "scan"}
    assert parsed[0].event_id == first.event_id


def test_consent_request_event_wraps_the_request():
    request = ConsentRequest(
        action="reboot_device", description="Reboot the customer's modem", risk="low"
    )
    event = consent_request_event(request, source="inhome-agent", session_id="s1")
    assert event.type is EventType.CONSENT_REQUEST
    assert event.data["consent_id"] == request.consent_id
    assert event.data["action"] == "reboot_device"
