"""Server-Sent Events helpers: read an event stream incrementally, and keep a stream alive."""

import asyncio
from collections.abc import AsyncIterator

import httpx

from poc_common.events import SSE_HEARTBEAT_COMMENT, Event, EventType


async def iter_sse_events(response: httpx.Response) -> AsyncIterator[Event]:
    """Yield events from a streaming httpx response as soon as each frame is complete."""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield Event.model_validate_json("\n".join(data_lines))
                data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield Event.model_validate_json("\n".join(data_lines))


async def sse_with_heartbeats(
    events: AsyncIterator[Event], interval_s: float = 15.0
) -> AsyncIterator[str]:
    """Encode events as SSE and send a comment line whenever nothing happened for `interval_s`.

    Local LLMs on CPU can be silent for a long time; heartbeats stop proxies and browsers from
    closing the connection.
    """
    queue: asyncio.Queue = asyncio.Queue()
    finished = object()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an error event
            await queue.put(Event(type=EventType.ERROR, data={"message": str(exc)}))
        finally:
            await queue.put(finished)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval_s)
            except TimeoutError:
                yield SSE_HEARTBEAT_COMMENT
                continue
            if item is finished:
                break
            yield item.to_sse()
    finally:
        task.cancel()
