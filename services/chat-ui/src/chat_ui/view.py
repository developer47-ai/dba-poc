"""Turn a stream of events into what the UI shows. Pure Python, so it is easy to test."""

from dataclasses import dataclass, field
from typing import Any

from poc_common.events import Event, EventType

TOOL_ERROR_PREFIX = "Tool error:"


@dataclass
class ToolStep:
    name: str
    arguments: dict[str, Any]
    status: str = "running"  # running | done | failed
    result: Any = None


@dataclass
class TurnView:
    agent: str | None = None
    status_label: str = "Routing…"
    log: list[str] = field(default_factory=list)
    tools: list[ToolStep] = field(default_factory=list)
    text: str = ""
    final: bool = False
    error: str | None = None
    done: bool = False
    message_id: str | None = None

    @property
    def summary_label(self) -> str:
        if self.error:
            return "Something went wrong"
        agent = self.agent or "agent"
        return f"{agent} · {len(self.tools)} tool call(s)"


def summarize(result: Any, limit: int = 140) -> str:
    if isinstance(result, dict):
        scalars = [
            f"{k}={v}" for k, v in result.items() if isinstance(v, str | int | float | bool)
        ]
        text = ", ".join(scalars[:4]) or f"{len(result)} field(s)"
    elif isinstance(result, list):
        text = f"{len(result)} item(s)"
    else:
        text = str(result)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def apply_event(view: TurnView, event: Event) -> str | None:
    """Update `view` with one event; return a new activity-log line, if any."""
    data = event.data
    line: str | None = None

    match event.type:
        case EventType.STATUS_UPDATE if data.get("step") == "routing":
            view.agent = data.get("agent")
            view.status_label = f"Routing to {view.agent}…"
            line = f"🧭 Routed to **{view.agent}** — {data.get('reason', '')}"
        case EventType.STATUS_UPDATE if data.get("step") == "diagnosis_started":
            view.status_label = f"{event.source} is diagnosing…"
            line = f"🩺 **{event.source}** started ({data.get('tools', '?')} tools available)"
        case EventType.STATUS_UPDATE if data.get("step") == "reasoning":
            view.status_label = f"🧠 {event.source} is reasoning (step {data.get('round', 1)})…"
        case EventType.STATUS_UPDATE if data.get("step") == "thinking":
            view.status_label = f"🧠 Thinking… ({data.get('tokens', 0)} tokens)"
        case EventType.AGENT_HANDOFF:
            line = f"🔀 Hand-over: {data.get('from_agent')} → **{data.get('to_agent')}**"
        case EventType.TOOL_CALL if data.get("status") == "started":
            view.tools.append(ToolStep(data["tool"], data.get("arguments") or {}))
            view.status_label = f"Running {data['tool']}…"
            line = f"🔧 Calling `{data['tool']}`"
        case EventType.TOOL_CALL if data.get("status") == "completed":
            running = [s for s in view.tools if s.name == data["tool"] and s.status == "running"]
            step = running[-1] if running else None
            result = data.get("result")
            failed = isinstance(result, str) and result.startswith(TOOL_ERROR_PREFIX)
            if step:
                step.status, step.result = ("failed" if failed else "done"), result
            icon = "⚠️" if failed else "✅"
            line = f"{icon} `{data['tool']}` → {summarize(result)}"
        case EventType.AGENT_MESSAGE_DELTA:
            if data.get("message_id") != view.message_id:
                view.message_id, view.text = data.get("message_id"), ""
            view.text += data.get("text", "")
            view.status_label = "Writing the answer…"
        case EventType.AGENT_MESSAGE if data.get("final", True):
            view.text, view.final = data.get("text", ""), True
        case EventType.AGENT_MESSAGE:
            # Text the model wrote before calling tools: keep it in the log, not as the answer.
            if data.get("message_id") == view.message_id:
                view.text = ""
            if data.get("text"):
                line = f"💭 {data['text']}"
        case EventType.ERROR:
            view.error = data.get("message", "unknown error")
            line = f"❌ {view.error}"
        case EventType.DONE:
            view.done = True
            view.status_label = view.summary_label
    if line:
        view.log.append(line)
    return line
