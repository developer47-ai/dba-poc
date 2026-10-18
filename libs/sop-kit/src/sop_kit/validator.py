"""Check that a document is complete and its procedure graph is sound.

Returns human-readable problems instead of raising, so authors see every issue at once.
"""

import re

from sop_kit.catalog import AGENTS, HANDOVER_TARGETS, TOOL_CATALOG
from sop_kit.models import Actor, DocKind, SopDocument, StepEnd

DOC_ID = re.compile(r"^(SOP|KB)-[A-Z][A-Z0-9]{1,4}-\d{3}$")
STEP_ID = re.compile(r"^S\d+$")


def validate(doc: SopDocument, *, known_doc_ids: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    add = problems.append

    if not DOC_ID.match(doc.doc_id):
        add(f"doc_id '{doc.doc_id}' must look like SOP-IH-001 or KB-LED-001")
    expected_prefix = "SOP-" if doc.kind is DocKind.PROCEDURE else "KB-"
    if not doc.doc_id.startswith(expected_prefix):
        add(f"a {doc.kind.value} document's id must start with '{expected_prefix}'")
    if not doc.title.strip():
        add("title is empty")
    for scope in doc.agent_scope:
        if scope not in AGENTS:
            add(f"agent_scope '{scope}' is not one of {sorted(AGENTS)}")
    if not doc.agent_scope:
        add("agent_scope is empty: say which agents may use this document")

    if doc.kind is DocKind.REFERENCE:
        if doc.steps:
            add("reference documents must not contain procedure steps")
        if not doc.sections:
            add("reference document has no sections")
        return problems

    problems.extend(_validate_procedure(doc, known_doc_ids))
    return problems


def _validate_procedure(doc: SopDocument, known_doc_ids: set[str] | None) -> list[str]:
    problems: list[str] = []
    add = problems.append
    if not doc.steps:
        return ["procedure has no steps under '## Procedure'"]

    ids = [s.step_id for s in doc.steps]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        add(f"step id {duplicate} is used more than once")
    if doc.entry_step not in ids:
        add(f"entry_step '{doc.entry_step}' is not a step of this procedure")

    for step in doc.steps:
        where = f"step {step.step_id}"
        if not STEP_ID.match(step.step_id):
            add(f"{where}: id must look like S1, S2, ...")
        if not step.instruction:
            add(f"{where}: has no instruction text")
        if step.next and step.end:
            add(f"{where}: has both 'next' and 'end'; use one")
        if not step.next and not step.end:
            add(f"{where}: needs 'next' outcomes or an 'end'")
        for outcome, target in step.next.items():
            if target not in ids:
                add(f"{where}: outcome '{outcome}' points to unknown step {target}")

        if step.tool:
            info = TOOL_CATALOG.get(step.tool)
            if info is None:
                add(f"{where}: unknown tool '{step.tool}' (use server.tool, see sop_kit.catalog)")
            elif info.changes_state and not step.requires_consent:
                add(f"{where}: '{step.tool}' changes the customer's service, "
                    "so it must set requires_consent: true")
            if step.actor is not Actor.AGENT:
                add(f"{where}: only actor 'agent' may call a tool")
        elif step.requires_consent:
            add(f"{where}: requires_consent is set but the step calls no tool")

        if step.end is StepEnd.ESCALATE and not step.escalate_to:
            add(f"{where}: end 'escalate' needs escalate_to (a team or an SOP id)")
        if step.end is StepEnd.HANDOVER and step.handover_to not in HANDOVER_TARGETS:
            add(f"{where}: end 'handover' needs handover_to, one of {sorted(HANDOVER_TARGETS)}")
        if step.end is StepEnd.CONTINUE and not step.continue_with:
            add(f"{where}: end 'continue' needs continue_with (an SOP id)")
        for reference in (step.continue_with, step.escalate_to):
            if (reference and reference.startswith("SOP-") and known_doc_ids is not None
                    and reference not in known_doc_ids):
                add(f"{where}: refers to {reference}, which is not in the library")

    if problems:
        return problems
    problems.extend(_validate_graph(doc))
    return problems


def _validate_graph(doc: SopDocument) -> list[str]:
    edges = {s.step_id: set(s.next.values()) for s in doc.steps}
    terminals = {s.step_id for s in doc.steps if s.is_terminal}

    reachable, frontier = set(), [doc.entry_step]
    while frontier:
        current = frontier.pop()
        if current not in reachable:
            reachable.add(current)
            frontier.extend(edges[current])

    # Steps from which some terminal step can be reached (walk the graph backwards).
    can_finish, changed = set(terminals), True
    while changed:
        changed = False
        for step_id, targets in edges.items():
            if step_id not in can_finish and targets & can_finish:
                can_finish.add(step_id)
                changed = True

    problems = [f"step {s} cannot be reached from {doc.entry_step}"
                for s in sorted(edges) if s not in reachable]
    problems += [f"step {s} can never reach an end (loop without exit)"
                 for s in sorted(reachable) if s not in can_finish]
    return problems


def validate_library(docs: list[SopDocument]) -> dict[str, list[str]]:
    """Validate every document plus library-wide rules; returns problems per doc_id."""
    known = {d.doc_id for d in docs}
    report = {d.doc_id: validate(d, known_doc_ids=known) for d in docs}
    for doc_id in sorted(known):
        if [d.doc_id for d in docs].count(doc_id) > 1:
            report[doc_id].append("doc_id is used by more than one file")
    return {doc_id: problems for doc_id, problems in report.items() if problems}
