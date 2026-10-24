"""One conversation turn: route, call the specialist agent, relay its events, keep history."""

from collections.abc import AsyncIterator

import httpx

from orchestrator.routing import Router
from orchestrator.sessions import Session
from poc_common.events import AgentRequest, ChatTurn, ConsentResponse, Event, EventType
from poc_common.logging import CORRELATION_HEADER
from poc_common.sse import iter_sse_events
from poc_common.tracing import current_trace_id, span

SOURCE = "orchestrator"


class AgentUnavailableError(RuntimeError):
    pass


async def stream_agent(
    client: httpx.AsyncClient, url: str, request: AgentRequest, correlation_id: str,
    agent: str = "",
) -> AsyncIterator[Event]:
    # The HTTP call below is traced by the httpx instrumentation and carries the trace context,
    # so the agent's own spans appear as children of this one.
    with span("agent.call", {"agent.name": agent, "agent.url": url,
                             "agent.resumes_state": request.state is not None,
                             "agent.consent": request.consent is not None}):
        try:
            async with client.stream(
                "POST",
                f"{url}/v1/diagnose",
                json=request.model_dump(),
                headers={CORRELATION_HEADER: correlation_id, "Accept": "text/event-stream"},
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise AgentUnavailableError(
                        f"HTTP {response.status_code}: {response.text[:200]}")
                async for event in iter_sse_events(response):
                    yield event
        except httpx.HTTPError as exc:
            raise AgentUnavailableError(str(exc) or type(exc).__name__) from exc


MAX_HANDOVERS = 2
# Agents see the latest messages only: the full chat stays stored, but a small local model has a
# limited context window and old turns would crowd out the current case.
MAX_AGENT_HISTORY = 12


def _handover_message(source: str, handover: dict, message: str) -> str:
    """What the next agent is told: where the case comes from, what was found, what was asked."""
    note = f"Handed over from {source} ({handover.get('from', 'procedure')})."
    if handover.get("context"):
        note += f" Findings so far: {handover['context']}."
    return f"{note} Expert's request: {message}"[:4000]


async def run_turn(
    session: Session,
    message: str,
    *,
    router: Router,
    client: httpx.AsyncClient,
    correlation_id: str,
    consent: ConsentResponse | None = None,
) -> AsyncIterator[Event]:
    """One turn as one trace: everything the agents and tools do hangs below the `turn` span."""
    with span("turn", {"session.id": session.session_id, "customer.id": session.customer_id,
                       "correlation.id": correlation_id,
                       "turn.kind": "approval" if consent else "message"}):
        trace_id = current_trace_id()
        if trace_id:
            # Lets the chat UI link to this turn's trace.
            yield Event(type=EventType.STATUS_UPDATE, source=SOURCE, session_id=session.session_id,
                        correlation_id=correlation_id, data={"step": "trace", "trace_id": trace_id})
        async for item in _run_turn(
            session, message, router=router, client=client, correlation_id=correlation_id,
            consent=consent,
        ):
            yield item


async def _run_turn(
    session: Session,
    message: str,
    *,
    router: Router,
    client: httpx.AsyncClient,
    correlation_id: str,
    consent: ConsentResponse | None = None,
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

    agent, text, hops = decision.agent, message, 0
    answers: list[tuple[str, str]] = []
    try:
        while True:
            request = AgentRequest(
                customer_id=session.customer_id,
                message=text,
                session_id=session.session_id,
                history=session.history[-MAX_AGENT_HISTORY:],
                state=session.agent_state.get(agent),
                consent=consent if hops == 0 else None,
            )
            handover = None
            async for item in stream_agent(
                client, router.registry.url(agent), request, correlation_id, agent
            ):
                if item.type is EventType.DONE:
                    continue
                if item.type is EventType.AGENT_STATE:
                    # Internal bookkeeping: keep it for the agent's next turn, do not show it.
                    state = item.data.get("state") or {}
                    handover = state.pop("handover", None)
                    session.agent_state[agent] = state
                    if (state.get("sop") or {}).get("awaiting") != "consent":
                        session.pending_consent = None
                    continue
                if item.type is EventType.CONSENT_REQUEST:
                    session.pending_consent = {**item.data, "agent": agent}
                item.session_id, item.correlation_id = session.session_id, correlation_id
                if item.type is EventType.AGENT_MESSAGE and item.data.get("final", True) \
                        and item.data.get("text"):
                    answers.append((agent, item.data["text"]))
                yield item

            if not handover:
                break
            target, why = await router.resolve_handover(handover["to"], session.customer_id)
            if target is None or target == agent or hops >= MAX_HANDOVERS:
                yield event(EventType.STATUS_UPDATE, step="handover_unavailable",
                            from_agent=agent, requested=handover["to"], reason=why)
                break
            yield event(EventType.AGENT_HANDOFF, from_agent=agent, to_agent=target,
                        reason=f"{handover.get('from', 'procedure')} → {why}",
                        context=handover.get("context", ""))
            session.active_agent = target
            text = _handover_message(agent, handover, message)
            agent, hops = target, hops + 1
    except AgentUnavailableError as exc:
        yield event(EventType.ERROR, message=f"The {agent} is not available: {exc}")

    # Only completed exchanges enter the history, so a failed turn can simply be retried.
    if answers:
        labelled = len({a for a, _ in answers}) > 1
        reply = "\n\n".join(f"[{a}] {t}" if labelled else t for a, t in answers)
        session.history.append(ChatTurn(role="user", content=message))
        session.history.append(ChatTurn(role="assistant", content=reply))
    yield event(EventType.DONE)