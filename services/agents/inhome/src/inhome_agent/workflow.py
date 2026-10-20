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

from inhome_agent.decide import Decision, choose
from inhome_agent.sop_prompts import (
    ANSWER_SYSTEM,
    DECIDE_SYSTEM,
    KNOWLEDGE_SYSTEM,
    answer_prompt,
    intent_prompt,
    knowledge_prompt,
    reply_outcome_prompt,
    step_outcome_prompt,
)
from poc_common.config import Config
from poc_common.events import AgentRequest, ConsentRequest, Event, EventType
from poc_common.llm import ThinkingFilter, apply_thinking_mode, strip_thinking
from poc_common.mcp_client import McpToolError, call_tool

logger = logging.getLogger(__name__)

AGENT = "inhome"
SOURCE = "inhome-agent"
MAX_STEPS_PER_TURN = 15
MAX_PROCEDURES_PER_TURN = 3
ANSWER_NODES = {"compose", "knowledge"}
THINKING_REPORT_EVERY = 25
HIGH_RISK_ACTIONS = {"factory_reset_device", "create_swap_order"}
NO_KNOWLEDGE_ANSWER = (
    "The approved NetCo knowledge base does not cover this question, so I cannot answer it "
    "reliably. Please check with your team lead."
)


class TurnState(TypedDict, total=False):
    message: str
    resume: dict | None      # SOP position this agent saved last turn
    intent: str
    route: str               # "sop" | "knowledge" | "exploratory"
    step: dict | None        # current SOP step (from the knowledge server)
    trail: list[dict]        # steps carried out this turn, with tool results and outcomes
    stop: dict | None        # why the procedure paused or ended this turn
    procedures: int
    steps_run: int
    answer: str
    sop_state: dict | None   # SOP position to save for the next turn


@dataclass
class TurnResult:
    route: str = ""


def _trail_item(step: dict, **extra: Any) -> dict:
    return {"citation": step["citation"], "doc_id": step["doc_id"], "step_id": step["step_id"],
            "title": step["title"], **extra}


def build_workflow(llm: BaseChatModel, config: Config, customer_id: str, session_id: str | None):
    tool_servers = {"network-diagnostics": config.diagnostics_mcp_url}

    def emit(type_: EventType, **data: Any) -> None:
        get_stream_writer()(Event(type=type_, source=SOURCE, session_id=session_id, data=data))

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

    async def intent(state: TurnState) -> dict:
        status("reasoning", purpose="intent")
        decision = await decide(intent_prompt(state["message"]), ["troubleshoot", "question"])
        chosen = decision.outcome or "troubleshoot"
        status("intent", intent=chosen, reason=decision.reason)
        return {"intent": chosen}

    async def select_sop(state: TurnState) -> dict:
        found = await knowledge("find_troubleshooting_sop", symptom=state["message"], agent=AGENT)
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
            action = (step["tool"] or "").partition(".")[2]
            consent = ConsentRequest(
                action=action, description=step["instruction"],
                risk="high" if action in HIGH_RISK_ACTIONS else "medium",
                arguments={"customer_id": customer_id}, sop_id=step["doc_id"],
                step_id=step["step_id"],
            )
            emit(EventType.CONSENT_REQUEST, **consent.model_dump())
            return {**update, "stop": {"kind": "consent", "step": step,
                                       "consent_id": consent.consent_id}}

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
                saved["consent_id"] = stop["consent_id"]
        status("reasoning", purpose="answer")
        prompt = answer_prompt(state["message"], state.get("trail") or [], stop)
        return {"answer": await stream_answer(ANSWER_SYSTEM, prompt), "sop_state": saved}

    async def knowledge_answer(state: TurnState) -> dict:
        hits = await knowledge("search_knowledge", query=state["message"], agent=AGENT, top_k=4)
        seen = set()
        for hit in hits:
            if hit["doc_id"] not in seen:
                seen.add(hit["doc_id"])
                emit(EventType.KNOWLEDGE_ARTICLE, doc_id=hit["doc_id"], title=hit["title"],
                     citation=hit["citation"], kind=hit["kind"])
        if not hits:
            return {"route": "knowledge", "answer": NO_KNOWLEDGE_ANSWER, "sop_state": None}
        status("reasoning", purpose="answer")
        answer = await stream_answer(KNOWLEDGE_SYSTEM, knowledge_prompt(state["message"], hits))
        return {"route": "knowledge", "answer": answer, "sop_state": None}

    # ---- graph ------------------------------------------------------------------------------

    def start(state: TurnState) -> str:
        saved = state.get("resume") or {}
        return "resume" if saved.get("awaiting") == "expert" else "intent"

    graph = StateGraph(TurnState)
    graph.add_node("resume", resume)
    graph.add_node("intent", intent)
    graph.add_node("select_sop", select_sop)
    graph.add_node("execute", execute)
    graph.add_node("compose", compose)
    graph.add_node("knowledge", knowledge_answer)
    graph.add_conditional_edges(START, start, ["resume", "intent"])
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
    request: AgentRequest, config: Config, llm: BaseChatModel, result: TurnResult
) -> AsyncIterator[Event]:
    """Stream the workflow's events; `result.route` tells the caller what happened."""
    workflow = build_workflow(llm, config, request.customer_id, request.session_id)

    def event(type_: EventType, **data: Any) -> Event:
        return Event(type=type_, source=SOURCE, session_id=request.session_id, data=data)

    stream_filter, message_id, streamed, hidden = ThinkingFilter(), None, False, 0
    final: TurnState = {}
    async for mode, payload in workflow.astream(
        {"message": request.message, "resume": (request.state or {}).get("sop")},
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
    yield event(EventType.AGENT_STATE, state={"sop": final.get("sop_state")})
