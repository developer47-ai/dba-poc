import asyncio

import httpx

from poc_common.events import Event, EventType
from poc_common.sse import iter_sse_events, sse_with_heartbeats


async def test_iter_sse_events_reads_frames_incrementally():
    first = Event(type=EventType.STATUS_UPDATE, data={"step": "a"})
    second = Event(type=EventType.DONE)
    body = first.to_sse() + ": heartbeat\n\n" + second.to_sse()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with client.stream("GET", "http://agent/stream") as response:
            events = [e async for e in iter_sse_events(response)]

    assert [e.event_id for e in events] == [first.event_id, second.event_id]


async def _events(*items, delay: float = 0.0):
    for item in items:
        await asyncio.sleep(delay)
        yield item


async def test_heartbeats_fill_silent_gaps():
    frames = [
        f async for f in sse_with_heartbeats(
            _events(Event(type=EventType.DONE), delay=0.25), interval_s=0.05
        )
    ]
    assert frames[0] == ": heartbeat\n\n"
    assert frames[-1].startswith("id: ") and "event: done" in frames[-1]


async def test_no_heartbeats_when_events_flow():
    frames = [f async for f in sse_with_heartbeats(_events(Event(type=EventType.DONE)), 5)]
    assert len(frames) == 1


async def test_source_failure_becomes_error_event():
    async def broken():
        yield Event(type=EventType.STATUS_UPDATE)
        raise RuntimeError("boom")

    frames = [f async for f in sse_with_heartbeats(broken(), 5)]
    assert "event: status_update" in frames[0]
    assert "event: error" in frames[1] and "boom" in frames[1]
