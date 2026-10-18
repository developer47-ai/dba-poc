"""One conversation turn: route, call the specialist agent, relay its events, keep history."""

from collections.abc import AsyncIterator

import httpx

from orchestrator.routing import Router
from orchestrator.sessions import Session
from poc_common.events import AgentRequest, ChatTurn, Event, EventType
from poc_common.logging import CORRELATION_HEADER
from poc_common.sse import iter_sse_events

SOURCE = "orchestrator"


class AgentUnavailableError(RuntimeError):
    pass


async def stream_agent(
    client: httpx.AsyncClient, url: str, request: AgentRequest, correlation_id: str
) -> AsyncIterator[Event]:
    try:
        async with client.stream(
            "POST",
            f"{url}/v1/diagnose",
            json=request.model_dump(),
            headers={CORRELATION_HEADER: correlation_id, "Accept": "text/event-stream"},
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise AgentUnavailableError(f"HTTP {response.status_code}: {response.text[:200]}")
            async for event in iter_sse_events(response):
                yield event
    except httpx.HTTPError as exc:
        raise AgentUnavailableError(str(exc) or type(exc).__name__) from exc


async def run_turn(
    session: Session,
    message: str,
    *,
    router: Router,
    client: httpx.AsyncClient,
    correlation_id: str,
) -> AsyncIterator[Event]:
    def event(type_: EventType, **data) -> Event:
        return Event(
            type=type_, source=SOURCE, session_id=session.session_id,
            correlation_id=correlation_id, data=data,
        )

    decision = router.route(session, message)
    yield event(EventType.STATUS_UPDATE, step="routing", agent=decision.agent,
                reason=decision.reason)
    if session.active_agent != decision.agent:
        yield event(EventType.AGENT_HANDOFF, from_agent=session.active_agent or SOURCE,
                    to_agent=decision.agent, reason=decision.reason)
        session.active_agent = decision.agent

    request = AgentRequest(
        customer_id=session.customer_id,
        message=message,
        session_id=session.session_id,
        history=session.history,
    )
    answer: list[str] = []
    try:
        async for item in stream_agent(
            client, router.registry.url(decision.agent), request, correlation_id
        ):
            if item.type is EventType.DONE:
                continue
            item.session_id, item.correlation_id = session.session_id, correlation_id
            if item.type is EventType.AGENT_MESSAGE and item.data.get("final", True) \
                    and item.data.get("text"):
                answer.append(item.data["text"])
            yield item
    except AgentUnavailableError as exc:
        yield event(EventType.ERROR, message=f"The {decision.agent} is not available: {exc}")

    # Only completed exchanges enter the history, so a failed turn can simply be retried.
    if answer:
        session.history.append(ChatTurn(role="user", content=message))
        session.history.append(ChatTurn(role="assistant", content="\n\n".join(answer)))
    yield event(EventType.DONE)
