"""SOP-guided troubleshooting as a LangGraph workflow.

    START ─┬─(waiting for the expert's reply)─▶ resume ─┬─▶ execute ⟲ ─▶ compose ─▶ END
           └─────────────────────────────────▶ intent ──┤
                                                        ├─ troubleshoot ─▶ select_sop ─▶ execute
                                                        │            (no SOP ─▶ END, exploratory)
                                                        └─ question ─▶ knowledge ─▶ END

`execute` runs one SOP step per pass: it calls the step's tool, lets the LLM pick the outcome
from the step's allowed outcomes, and asks the knowledge server for the next step. It stops at
steps that need approval, at questions for the expert or customer, and at the end of a
procedure. Progress is streamed as events; the answer is streamed token by token.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from poc_common.config import Config
from poc_common.events import AgentRequest, ConsentRequest, Event, EventType
from poc_common.llm import ThinkingFilter, apply_thinking_mode, strip_thinking
from poc_common.mcp_client import McpToolError, call_tool
from sop_agent.decide import Decision, choose
from sop_agent.sop_prompts import (
    DECIDE_SYSTEM,
    answer_prompt,
    answer_system,
    intent_prompt,
    knowledge_prompt,
    knowledge_system,
    reply_outcome_prompt,
    step_outcome_prompt,
)
from sop_agent.spec import AgentSpec

logger = logging.getLogger(__name__)

MAX_STEPS_PER_TURN = 15
MAX_PROCEDURES_PER_TURN = 3
ANSWER_NODES = {"compose", "knowledge"}
THINKING_REPORT_EVERY = 25
HIGH_RISK_ACTIONS = {"factory_reset_device", "create_swap_order"}
NO_KNOWLEDGE_ANSWER = (
    "The approved NetCo knowledge base does not cover this question, so I cannot answer it "
    "reliably. Please check with your team lead."
)
REJECTED_ANSWER = (
    "I cannot carry out that decision: no matching approval request is pending, so nothing was "
    "changed. Please tell me what you would like to do."
)


class TurnState(TypedDict, total=False):
    message: str
    resume: dict | None      # SOP position this agent saved last turn
    consent: dict | None     # the expert's decision on the pending approval, if this turn has one
    intent: str
    route: str               # "sop" | "knowledge" | "exploratory"
    step: dict | None        # current SOP step (from the knowledge server)
    trail: list[dict]        # steps carried out this turn, with tool results and outcomes
    stop: dict | None        # why the procedure paused or ended this turn
    procedures: int
    steps_run: int
    answer: str
    sop_state: dict | None   # SOP position to save for the next turn
    handover: dict | None    # set when the procedure ends by handing the case to another agent


@dataclass
class TurnResult:
    route: str = ""


def _trail_item(step: dict, **extra: Any) -> dict:
    return {"citation": step["citation"], "doc_id": step["doc_id"], "step_id": step["step_id"],
            "title": step["title"], **extra}


def _handover_context(trail: list[dict]) -> str:
    """What the next agent should know: the checks done and what they showed."""
    parts = []
    for item in trail:
        if item.get("outcome"):
            reason = f" ({item['reason']})" if item.get("reason") else ""
            parts.append(f"{item['title']}: {item['outcome']}{reason}")
    return "; ".join(parts)[:600]


def consent_arguments(
    action: str, trail: list[dict], customer_id: str
) -> tuple[dict | None, str]:
    """Arguments to run once approved, plus a sentence telling the expert exactly what they approve.

    Returns (None, reason) when the action cannot be proposed.
    """
    arguments: dict[str, Any] = {"customer_id": customer_id}
    if action != "book_appointment":
        return arguments, ""
    for item in reversed(trail):
        result = item.get("result")
        tool_name = item.get("tool") or ""
        if tool_name.endswith("search_appointment_slots") and isinstance(result, dict):
            slots = result.get("slots") or []
            if slots:
                slot = slots[0]
                arguments["slot_id"] = slot["slot_id"]
                when = f"{slot['weekday']} {slot['date']}, {slot['from']}-{slot['to']}"
                return arguments, f"Proposed slot: {when}."
    return None, "no free appointment slot was found to propose"


def build_workflow(
    llm: BaseChatModel, config: Config, spec: AgentSpec, customer_id: str, session_id: str | None
):
    tool_servers = {
        "network-diagnostics": config.diagnostics_mcp_url,
        "scheduling": config.scheduling_mcp_url,
    }

    def emit(type_: EventType, **data: Any) -> None:
        get_stream_writer()(Event(type=type_, source=spec.source, session_id=session_id, data=data))

    def status(step: str, **data: Any) -> None:
        emit(EventType.STATUS_UPDATE, step=step, **data)

    async def knowledge(tool: str, **arguments: Any) -> Any:
        return await call_tool(config.knowledge_mcp_url, tool, arguments)

    async def decide(prompt: str, options: list[str]) -> Decision:
        return await choose(llm, apply_thinking_mode(DECIDE_SYSTEM, config), prompt, options)

    async def stream_answer(system: str, prompt: str) -> str:
        parts = []
        async for chunk in llm.astream(
            [SystemMessage(apply_thinking_mode(system, config)), HumanMessage(prompt)]
        ):
            parts.append(chunk.text)
        return strip_thinking("".join(parts))

    def announce_procedure(doc_id: str, title: str, version: str, **extra: Any) -> None:
        status("sop_selected", doc_id=doc_id, title=title, version=version, **extra)
        emit(EventType.KNOWLEDGE_ARTICLE, doc_id=doc_id, title=title,
             citation=f"{doc_id} v{version}", kind="procedure")

    # ---- nodes ------------------------------------------------------------------------------

    async def resume(state: TurnState) -> dict:
        saved = state["resume"]
        step = await knowledge("get_sop_step", doc_id=saved["doc_id"], step_id=saved["step_id"])
        status("reasoning", purpose="outcome", step_id=step["step_id"])
        decision = await decide(reply_outcome_prompt(step, state["message"]), step["outcomes"])
        if decision.outcome is None:
            # The reply is about something else: treat the message as a new request.
            status("sop_resume_skipped", doc_id=step["doc_id"], step_id=step["step_id"])
            return {"resume": None}
        status("sop_outcome", doc_id=step["doc_id"], step_id=step["step_id"],
               outcome=decision.outcome, reason=decision.reason, source="expert")
        moved = await knowledge("get_next_sop_step", doc_id=step["doc_id"],
                                step_id=step["step_id"], outcome=decision.outcome)
        item = _trail_item(step, outcome=decision.outcome, reason=decision.reason,
                           reported_by="expert")
        return {"route": "sop", "step": moved["next_step"], "trail": [item], "procedures": 1}

    async def act(state: TurnState) -> dict:
        """Carry out (or skip) the action the expert just approved (or declined), then go on."""
        saved, decision = state.get("resume") or {}, state["consent"]

        def reject(reason: str) -> dict:
            status("consent_rejected", consent_id=decision["consent_id"], reason=reason)
            return {"route": "rejected", "answer": REJECTED_ANSWER, "sop_state": saved or None}

        if saved.get("awaiting") != "consent" or saved.get("consent_id") != decision["consent_id"]:
            return reject("no matching approval request is pending")
        step = await knowledge("get_sop_step", doc_id=saved["doc_id"], step_id=saved["step_id"])
        server, _, action = (step["tool"] or "").partition(".")
        arguments = saved.get("arguments") or {}
        if saved.get("action") != action or arguments.get("customer_id") != customer_id:
            return reject("the saved request does not match the procedure step")

        approved = decision["approved"]
        status("consent_resolved", consent_id=decision["consent_id"], action=action,
               approved=approved, comment=decision.get("comment"), sop_id=step["doc_id"],
               step_id=step["step_id"])
        result: Any = None
        if approved:
            outcome = next(o for o in step["outcomes"] if o != "declined")
            emit(EventType.TOOL_CALL, tool=action, arguments=arguments, status="started",
                 step_id=step["step_id"])
            try:
                result = await call_tool(tool_servers[server], action, arguments)
            except McpToolError as exc:
                result = f"Tool error: {exc}"
            emit(EventType.TOOL_CALL, tool=action, status="completed", result=result,
                 step_id=step["step_id"])
            if isinstance(result, str) and result.startswith("Tool error:"):
                item = _trail_item(step, tool=step["tool"], result=result, outcome="failed",
                                   reason="the approved action failed")
                return {"route": "sop", "trail": [item], "procedures": 1, "stop": {
                    "kind": "blocked", "step": step,
                    "detail": f"the approved action {action} failed: {result}"}}
            reason = "approved by the expert"
        else:
            outcome, reason = "declined", "declined by the expert"
        if decision.get("comment"):
            reason += f" ({decision['comment']})"

        status("sop_outcome", doc_id=step["doc_id"], step_id=step["step_id"], outcome=outcome,
               reason=reason, source="expert")
        moved = await knowledge("get_next_sop_step", doc_id=step["doc_id"],
                                step_id=step["step_id"], outcome=outcome)
        item = _trail_item(step, tool=step["tool"] if approved else None,
                           result=result, outcome=outcome, reason=reason)
        return {"route": "sop", "step": moved["next_step"], "trail": [item], "procedures": 1}

    async def intent(state: TurnState) -> dict:
        status("reasoning", purpose="intent")
        decision = await decide(intent_prompt(state["message"]), ["troubleshoot", "question"])
        chosen = decision.outcome or "troubleshoot"
        status("intent", intent=chosen, reason=decision.reason)
        return {"intent": chosen}

    async def select_sop(state: TurnState) -> dict:
        found = await knowledge(
            "find_troubleshooting_sop", symptom=state["message"], agent=spec.agent_id
        )
        if not found["found"]:
            status("no_sop")
            return {"route": "exploratory"}
        best = found["candidates"][0]
        announce_procedure(best["doc_id"], best["title"], best["version"],
                           matched=best["matched"], score=best["score"])
        step = await knowledge("get_sop_step", doc_id=best["doc_id"], step_id=best["entry_step"])
        return {"route": "sop", "step": step, "procedures": 1, "trail": []}

    async def execute(state: TurnState) -> dict:
        step, trail = state["step"], list(state.get("trail") or [])
        steps_run = state.get("steps_run", 0) + 1
        update: dict[str, Any] = {"trail": trail, "steps_run": steps_run}
        status("sop_step", doc_id=step["doc_id"], step_id=step["step_id"], title=step["title"],
               actor=step["actor"], citation=step["citation"])

        if steps_run > MAX_STEPS_PER_TURN:
            return {**update, "stop": {"kind": "blocked", "step": step,
                                       "detail": "the procedure took too many steps in one turn"}}

        if step.get("end"):
            target = step.get("continue_with") or (
                step.get("escalate_to") if step["end"] == "escalate" else None)
            procedures = state.get("procedures", 1)
            if target and target.startswith("SOP-") and procedures < MAX_PROCEDURES_PER_TURN:
                procedure = await knowledge("get_sop_procedure", doc_id=target)
                announce_procedure(procedure["doc_id"], procedure["title"], procedure["version"],
                                   via=step["citation"])
                trail.append(_trail_item(step, outcome=f"{step['end']} → {target}"))
                entry = await knowledge("get_sop_step", doc_id=target,
                                        step_id=procedure["entry_step"])
                return {**update, "step": entry, "procedures": procedures + 1}
            return {**update, "stop": {"kind": "end", "step": step}}

        if step["requires_consent"]:
            server, _, action = (step["tool"] or "").partition(".")
            if server not in tool_servers:
                return {**update, "stop": {
                    "kind": "blocked", "step": step,
                    "detail": f"{step['tool']} is not part of this demo, so the approved action "
                              "cannot be carried out here"}}
            arguments, proposal = consent_arguments(action, trail, customer_id)
            if arguments is None:
                return {**update, "stop": {"kind": "blocked", "step": step, "detail": proposal}}
            consent = ConsentRequest(
                action=action, description=f"{step['instruction']} {proposal}".strip(),
                risk="high" if action in HIGH_RISK_ACTIONS else "medium",
                arguments=arguments, sop_id=step["doc_id"], step_id=step["step_id"],
            )
            emit(EventType.CONSENT_REQUEST, **consent.model_dump())
            return {**update, "stop": {
                "kind": "consent", "step": step, "consent_id": consent.consent_id,
                "action": action, "arguments": consent.arguments}}

        if step["actor"] != "agent":
            return {**update, "stop": {"kind": "ask", "step": step}}

        result = None
        if step["tool"]:
            server, _, name = step["tool"].partition(".")
            url = tool_servers.get(server)
            if url is None:
                return {**update, "stop": {"kind": "blocked", "step": step,
                                           "detail": f"{step['tool']} is not available yet"}}
            emit(EventType.TOOL_CALL, tool=name, arguments={}, status="started",
                 step_id=step["step_id"])
            try:
                result = await call_tool(url, name, {"customer_id": customer_id})
            except McpToolError as exc:
                result = f"Tool error: {exc}"
            emit(EventType.TOOL_CALL, tool=name, status="completed", result=result,
                 step_id=step["step_id"])

        if len(step["outcomes"]) == 1:
            decision = Decision(step["outcomes"][0], "the only outcome of this step")
        else:
            status("reasoning", purpose="outcome", step_id=step["step_id"])
            decision = await decide(step_outcome_prompt(step, result), step["outcomes"])

        trail.append(_trail_item(step, tool=step["tool"], result=result,
                                 outcome=decision.outcome or "unclear", reason=decision.reason))
        status("sop_outcome", doc_id=step["doc_id"], step_id=step["step_id"],
               outcome=decision.outcome, reason=decision.reason, source="agent")
        if decision.outcome is None:
            return {**update, "stop": {"kind": "unclear", "step": step}}

        moved = await knowledge("get_next_sop_step", doc_id=step["doc_id"],
                                step_id=step["step_id"], outcome=decision.outcome)
        return {**update, "step": moved["next_step"]}

    async def compose(state: TurnState) -> dict:
        stop, step = state["stop"], state["stop"]["step"]
        saved = None
        if stop["kind"] in {"ask", "unclear", "consent"}:
            saved = {"doc_id": step["doc_id"], "version": step["version"],
                     "step_id": step["step_id"],
                     "awaiting": "consent" if stop["kind"] == "consent" else "expert"}
            if stop["kind"] == "consent":
                saved.update(consent_id=stop["consent_id"], action=stop["action"],
                             arguments=stop["arguments"])
        handover = None
        if stop["kind"] == "end" and step.get("end") == "handover":
            handover = {"to": step["handover_to"], "from": step["citation"],
                        "context": _handover_context(state.get("trail") or [])}
        status("reasoning", purpose="answer")
        prompt = answer_prompt(state["message"], state.get("trail") or [], stop)
        return {"answer": await stream_answer(answer_system(spec), prompt), "sop_state": saved,
                "handover": handover}

    async def knowledge_answer(state: TurnState) -> dict:
        hits = await knowledge(
            "search_knowledge", query=state["message"], agent=spec.agent_id, top_k=4
        )
        seen = set()
        for hit in hits:
            if hit["doc_id"] not in seen:
                seen.add(hit["doc_id"])
                emit(EventType.KNOWLEDGE_ARTICLE, doc_id=hit["doc_id"], title=hit["title"],
                     citation=hit["citation"], kind=hit["kind"])
        if not hits:
            return {"route": "knowledge", "answer": NO_KNOWLEDGE_ANSWER, "sop_state": None}
        status("reasoning", purpose="answer")
        answer = await stream_answer(
            knowledge_system(spec), knowledge_prompt(state["message"], hits)
        )
        return {"route": "knowledge", "answer": answer, "sop_state": None}

    # ---- graph ------------------------------------------------------------------------------

    def start(state: TurnState) -> str:
        if state.get("consent"):
            return "act"
        saved = state.get("resume") or {}
        return "resume" if saved.get("awaiting") == "expert" else "intent"

    graph = StateGraph(TurnState)
    graph.add_node("resume", resume)
    graph.add_node("act", act)
    graph.add_node("intent", intent)
    graph.add_node("select_sop", select_sop)
    graph.add_node("execute", execute)
    graph.add_node("compose", compose)
    graph.add_node("knowledge", knowledge_answer)
    graph.add_conditional_edges(START, start, ["act", "resume", "intent"])
    graph.add_conditional_edges(
        "act",
        lambda s: "compose" if s.get("stop") else ("execute" if s.get("step") else END),
        ["compose", "execute", END],
    )
    graph.add_conditional_edges(
        "resume", lambda s: "execute" if s.get("step") else "intent", ["execute", "intent"])
    graph.add_conditional_edges(
        "intent", lambda s: "knowledge" if s["intent"] == "question" else "select_sop",
        ["knowledge", "select_sop"])
    graph.add_conditional_edges(
        "select_sop", lambda s: "execute" if s["route"] == "sop" else END, ["execute", END])
    graph.add_conditional_edges(
        "execute", lambda s: "compose" if s.get("stop") else "execute", ["compose", "execute"])
    graph.add_edge("compose", END)
    graph.add_edge("knowledge", END)
    return graph.compile()


async def run_workflow(
    request: AgentRequest, config: Config, llm: BaseChatModel, spec: AgentSpec,
    result: TurnResult,
) -> AsyncIterator[Event]:
    """Stream the workflow's events; `result.route` tells the caller what happened."""
    workflow = build_workflow(llm, config, spec, request.customer_id, request.session_id)

    def event(type_: EventType, **data: Any) -> Event:
        return Event(type=type_, source=spec.source, session_id=request.session_id, data=data)

    stream_filter, message_id, streamed, hidden = ThinkingFilter(), None, False, 0
    final: TurnState = {}
    async for mode, payload in workflow.astream(
        {"message": request.message, "resume": (request.state or {}).get("sop"),
         "consent": request.consent.model_dump() if request.consent else None},
        {"recursion_limit": 3 * MAX_STEPS_PER_TURN},
        stream_mode=["custom", "messages", "values"],
    ):
        if mode == "custom":
            yield payload
        elif mode == "messages":
            chunk, metadata = payload
            if metadata.get("langgraph_node") not in ANSWER_NODES:
                continue
            if not isinstance(chunk, AIMessageChunk):
                continue
            message_id = message_id or chunk.id or uuid.uuid4().hex
            text = stream_filter.feed(chunk.text)
            if text:
                streamed = True
                yield event(EventType.AGENT_MESSAGE_DELTA, message_id=message_id, text=text)
            elif stream_filter.thinking:
                hidden += 1
                if hidden % THINKING_REPORT_EVERY == 0:
                    yield event(EventType.STATUS_UPDATE, step="thinking", tokens=hidden)
        else:
            final = payload

    result.route = final.get("route", "")
    if result.route == "exploratory":
        return
    answer = final.get("answer", "")
    message_id = message_id or uuid.uuid4().hex
    rest = stream_filter.flush()
    if rest:
        yield event(EventType.AGENT_MESSAGE_DELTA, message_id=message_id, text=rest)
    elif not streamed and answer:
        yield event(EventType.AGENT_MESSAGE_DELTA, message_id=message_id, text=answer)
    yield event(EventType.AGENT_MESSAGE, message_id=message_id, text=answer, final=True)
    yield event(EventType.AGENT_STATE,
                state={"sop": final.get("sop_state"), "handover": final.get("handover")})
