"""Prompts for the SOP-guided workflow (the exploratory fallback uses prompts.SYSTEM_PROMPT)."""

import json
from typing import Any

DECIDE_SYSTEM = (
    "You are a careful classifier in NetCo's customer support system. Read the facts and decide "
    "which allowed outcome applies. Call the report_outcome function exactly once. Choose "
    '"unclear" when the facts do not let you decide; never guess.'
)

ANSWER_SYSTEM = """You are the In-Home Agent of NetCo, a telecom operator, writing to a customer \
support expert. Use ONLY the facts given. Never claim an action was done unless the facts say so.

Write short, plain English with exactly these three parts:
Findings: what the checks showed.
Diagnosis: the most likely cause.
Next step: what the expert should do now.

After each finding and the next step, cite its source in square brackets exactly as given, \
for example [SOP-IH-001 › S2]."""

KNOWLEDGE_SYSTEM = """You are the In-Home Agent of NetCo, answering a customer support expert's \
question. Answer ONLY from the numbered passages. If they do not contain the answer, say that \
the approved knowledge base does not cover it. Keep it short and cite the passages you used in \
square brackets exactly as given, for example [KB-LED-001 v1.0]."""

MAX_RESULT_CHARS = 1200


def compact(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return text if len(text) <= MAX_RESULT_CHARS else text[: MAX_RESULT_CHARS - 1] + "…"


def intent_prompt(message: str) -> str:
    return (
        "Classify the support expert's message.\n"
        "troubleshoot: they report a customer's problem that needs diagnosis "
        "(no internet, Wi-Fi not working, a device cannot connect, a light shows a fault).\n"
        "question: they ask for information or an explanation "
        "(what a light means, how something works, what a setting does).\n\n"
        f'Message: "{message}"'
    )


def step_outcome_prompt(step: dict, tool_result: Any | None) -> str:
    lines = [
        f"Procedure {step['doc_id']}, step {step['step_id']} — {step['title']}.",
        f"Instruction (contains the decision rules):\n{step['instruction']}",
    ]
    if tool_result is not None:
        lines.append(f"Result of {step['tool']}:\n{compact(tool_result)}")
    lines.append(f"Allowed outcomes: {', '.join(step['outcomes'])}")
    lines.append("Which outcome do the instruction's rules give for this result?")
    return "\n\n".join(lines)


def reply_outcome_prompt(step: dict, reply: str) -> str:
    return (
        f"The support expert was asked to carry out step {step['step_id']} — {step['title']} of "
        f"procedure {step['doc_id']}:\n{step['instruction']}\n\n"
        f'Their reply: "{reply}"\n\n'
        f"Allowed outcomes: {', '.join(step['outcomes'])}\n"
        "Which outcome does the reply report? If the reply is about something else, "
        'choose "unclear".'
    )


def answer_prompt(message: str, trail: list[dict], stop: dict) -> str:
    lines = [f'Expert\'s message: "{message}"', ""]
    if trail:
        lines.append("Steps carried out:")
        for item in trail:
            line = f"- [{item['citation']}] {item['title']}"
            if item.get("tool"):
                line += f" — {item['tool']} returned {compact(item.get('result'))}"
            if item.get("outcome"):
                line += f" — outcome: {item['outcome']}"
                if item.get("reason"):
                    line += f" ({item['reason']})"
            lines.append(line)
        lines.append("")
    lines.append("Where the procedure stands now:")
    lines.append(_stop_text(stop))
    return "\n".join(lines)


def _stop_text(stop: dict) -> str:
    step = stop.get("step") or {}
    cite = f"[{step['citation']}]" if step.get("citation") else ""
    kind = stop["kind"]
    if kind == "consent":
        return (
            f"APPROVAL NEEDED {cite} — {step['title']}: {step['instruction']}\n"
            "The action has NOT been done. Ask the expert to approve it."
        )
    if kind == "ask":
        who = "ask the customer to" if step.get("actor") == "customer" else "the expert should"
        return (
            f"WAITING FOR THE EXPERT {cite} — {step['title']}: {who} do this: "
            f"{step['instruction']}\nThen the expert reports the result "
            f"(one of: {', '.join(step.get('outcomes', []))})."
        )
    if kind == "unclear":
        return (
            f"UNCLEAR {cite} — the result of {step['title']} did not match the procedure's rules. "
            f"Ask the expert which applies: {', '.join(step.get('outcomes', []))}."
        )
    if kind == "end":
        target = step.get("escalate_to") or step.get("handover_to") or ""
        return (
            f"PROCEDURE ENDS ({step.get('end')}{' → ' + target if target else ''}) {cite} — "
            f"{step['title']}: {step['instruction']}"
        )
    if kind == "blocked":
        return f"CANNOT CONTINUE {cite} — {stop.get('detail', '')}"
    return stop.get("detail", "")


def knowledge_prompt(question: str, passages: list[dict]) -> str:
    parts = [f'Question: "{question}"', "", "Passages:"]
    for number, passage in enumerate(passages, start=1):
        parts.append(f"{number}. [{passage['citation']}] {passage['text']}")
    return "\n".join(parts)
