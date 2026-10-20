"""Let the LLM pick one option from a fixed list, via a function call.

The model must call `report_outcome(outcome, reason)` where `outcome` is an enum of the allowed
values (plus "unclear"). Small local models sometimes answer in text instead, so the reply is
also parsed for JSON or a single allowed word. Anything else counts as "unclear": the caller then
asks the expert instead of guessing.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

TOOL_NAME = "report_outcome"
UNCLEAR = "unclear"
JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)


@dataclass(frozen=True)
class Decision:
    outcome: str | None  # None = unclear
    reason: str


def outcome_tool(options: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Report which of the allowed outcomes applies, with a short reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": [*options, UNCLEAR]},
                    "reason": {
                        "type": "string",
                        "description": "One short sentence citing the evidence.",
                    },
                },
                "required": ["outcome", "reason"],
            },
        },
    }


def _normalise(value: Any, options: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    wanted = value.strip().strip("`'\"").lower().replace(" ", "_").replace("-", "_")
    return next((o for o in options if o.lower() == wanted), None)


def parse_decision(reply: AIMessage, options: list[str]) -> Decision:
    for call in reply.tool_calls:
        if call["name"] == TOOL_NAME:
            args = call.get("args") or {}
            return Decision(_normalise(args.get("outcome"), options), str(args.get("reason", "")))

    text = reply.text or ""
    for blob in JSON_OBJECT.findall(text):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "outcome" in data:
            return Decision(_normalise(data["outcome"], options), str(data.get("reason", "")))

    # Last resort: exactly one allowed option named as a whole word in the text.
    lowered = text.lower()
    named = {o for o in options if re.search(rf"(?<![\w]){re.escape(o.lower())}(?![\w])", lowered)}
    if len(named) == 1:
        return Decision(named.pop(), text.strip()[:200])
    return Decision(None, text.strip()[:200] or "no decision returned")


async def choose(
    llm: BaseChatModel, system_prompt: str, prompt: str, options: list[str]
) -> Decision:
    model = llm.bind_tools([outcome_tool(options)])
    reply = await model.ainvoke([SystemMessage(system_prompt), HumanMessage(prompt)])
    return parse_decision(reply, options)
