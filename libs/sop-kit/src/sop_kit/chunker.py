"""Split documents into search chunks that keep their structure.

- one *summary* chunk per document (title, symptoms, scope, purpose) to find the right SOP,
- one chunk per section (tables stay whole, long text is split on paragraphs),
- one chunk per procedure step, so a citation can point to e.g. SOP-IH-001 › S3.

Every chunk starts with a context line ("SOP-IH-001 v1.0 › Title › Section") so it still makes
sense on its own, and carries the document metadata for filtering.
"""

from sop_kit.models import Chunk, SopDocument, Step

MAX_CHARS = 1800


def chunk_document(doc: SopDocument) -> list[Chunk]:
    chunks: list[Chunk] = []

    def add(chunk_type: str, path: list[str], text: str, step_id: str | None = None) -> None:
        header = " › ".join([f"{doc.doc_id} v{doc.version}", doc.title, *path])
        chunks.append(Chunk(
            chunk_id=f"{doc.doc_id}:{doc.version}:{len(chunks)}",
            doc_id=doc.doc_id, version=doc.version, chunk_type=chunk_type,
            section_path=path, step_id=step_id, text=f"{header}\n{text.strip()}",
            metadata=doc.metadata(),
        ))

    add("summary", ["Summary"], _summary(doc))
    for section in doc.sections:
        for part in _split(section.text):
            add("section", [section.title], part)
    for step in doc.steps:
        add("step", ["Procedure", f"{step.step_id} — {step.title}"], _step_text(step),
            step.step_id)
    return chunks


def _summary(doc: SopDocument) -> str:
    lines = [f"{doc.kind.value.title()}: {doc.title}"]
    if doc.symptoms:
        lines.append("Symptoms: " + "; ".join(doc.symptoms))
    if doc.carrier_tech_types:
        lines.append("Technologies: " + ", ".join(doc.carrier_tech_types))
    if doc.device_models:
        lines.append("Devices: " + ", ".join(doc.device_models))
    lines.append("Used by: " + ", ".join(doc.agent_scope))
    first = next((s for s in doc.sections if s.title.lower() in {"purpose", "when to use"}), None)
    if first:
        lines.append(first.text)
    return "\n".join(lines)


def _step_text(step: Step) -> str:
    lines = [f"Actor: {step.actor.value}"]
    if step.tool:
        approval = " (requires approval)" if step.requires_consent else ""
        lines.append(f"Tool: {step.tool}{approval}")
    if step.next:
        lines.append("Outcomes: " + ", ".join(f"{o} → {t}" for o, t in step.next.items()))
    if step.end:
        target = step.escalate_to or step.handover_to or step.continue_with
        lines.append(f"End: {step.end.value}" + (f" → {target}" if target else ""))
    lines.append(step.instruction)
    return "\n".join(lines)


def _split(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, current = [], ""
    for block in text.split("\n\n"):
        is_table = block.lstrip().startswith("|")
        if current and (len(current) + len(block) > MAX_CHARS) and not is_table:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts
