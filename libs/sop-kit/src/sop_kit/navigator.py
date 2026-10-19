"""Walk a procedure deterministically: the agent reports an outcome, the code picks the next step.

The model only interprets tool results and phrases the step for the expert; it never decides the
path, so it cannot skip or invent steps.
"""

from typing import Any

from sop_kit.models import SopDocument, Step


class NavigationError(ValueError):
    """The step or outcome does not exist in this procedure."""


def step_view(doc: SopDocument, step: Step) -> dict[str, Any]:
    """Everything an agent needs to carry out one step."""
    view: dict[str, Any] = {
        "doc_id": doc.doc_id,
        "version": doc.version,
        "step_id": step.step_id,
        "title": step.title,
        "actor": step.actor.value,
        "instruction": step.instruction,
        "tool": step.tool,
        "requires_consent": step.requires_consent,
        "outcomes": list(step.next),
        "citation": f"{doc.doc_id} v{doc.version} › {step.step_id}",
    }
    if step.end:
        view["end"] = step.end.value
        for key in ("escalate_to", "handover_to", "continue_with"):
            if getattr(step, key):
                view[key] = getattr(step, key)
    return view


def procedure_view(doc: SopDocument) -> dict[str, Any]:
    if not doc.steps:
        raise NavigationError(f"{doc.doc_id} is a {doc.kind.value} document without steps")
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "version": doc.version,
        "entry_step": doc.entry_step,
        "steps": [step_view(doc, s) for s in doc.steps],
    }


def get_step(doc: SopDocument, step_id: str) -> dict[str, Any]:
    try:
        return step_view(doc, doc.step(step_id))
    except KeyError:
        valid = ", ".join(s.step_id for s in doc.steps)
        raise NavigationError(f"{doc.doc_id} has no step {step_id}; steps: {valid}") from None


def next_step(doc: SopDocument, step_id: str, outcome: str) -> dict[str, Any]:
    """The step that follows `step_id` when `outcome` was observed."""
    current = get_step(doc, step_id)
    step = doc.step(step_id)
    if step.is_terminal:
        raise NavigationError(
            f"{doc.doc_id} {step_id} is an end step ({step.end.value}); there is no next step"
        )
    target = step.next.get(outcome.strip())
    if target is None:
        raise NavigationError(
            f"'{outcome}' is not an outcome of {doc.doc_id} {step_id}; "
            f"choose one of: {', '.join(step.next)}"
        )
    return {
        "doc_id": doc.doc_id,
        "from_step": current["step_id"],
        "outcome": outcome.strip(),
        "next_step": step_view(doc, doc.step(target)),
    }
