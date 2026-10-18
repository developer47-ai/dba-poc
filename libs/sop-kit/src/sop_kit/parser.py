"""Parse SOP Markdown files: YAML front matter + sections + `### S1 — Title` procedure steps.

Step format (inside `## Procedure`):

    ### S2 — Reboot the modem
    - actor: agent
    - tool: network-diagnostics.reboot_device
    - requires_consent: true
    - next: { done: S3, declined: S9 }
    Free text instruction for the agent / expert...
"""

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from sop_kit.models import Section, SopDocument, Step

FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
H2 = re.compile(r"^##\s+(?!#)(.+?)\s*$", re.MULTILINE)
STEP_HEADING = re.compile(r"^###\s+([A-Z]\d+)\s*[—–:-]\s*(.+?)\s*$", re.MULTILINE)
STEP_FIELD = re.compile(r"^-\s+([a-z_]+):\s*(.*)$")
STEP_FIELDS = {
    "actor", "tool", "requires_consent", "next", "end",
    "escalate_to", "handover_to", "continue_with",
}
PROCEDURE_SECTION = "procedure"


class SopParseError(ValueError):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


def parse_sop(text: str, source: str = "<string>") -> SopDocument:
    match = FRONT_MATTER.match(text)
    if not match:
        raise SopParseError(source, "missing YAML front matter between '---' lines")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SopParseError(source, f"invalid front matter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SopParseError(source, "front matter must be a mapping")

    body = text[match.end():]
    sections, steps = _parse_body(body, source)
    meta = {k: (str(v) if k in {"version", "effective_from"} and v is not None else v)
            for k, v in meta.items()}
    try:
        return SopDocument(
            **meta,
            sections=sections,
            steps=steps,
            source=source,
            checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    except ValueError as exc:
        raise SopParseError(source, f"invalid document fields: {exc}") from exc


def load_sop(path: str | Path) -> SopDocument:
    path = Path(path)
    return parse_sop(path.read_text(encoding="utf-8"), source=path.as_posix())


def load_library(directory: str | Path) -> list[SopDocument]:
    """All `*.md` documents below `directory`, sorted by path (README.md files are skipped)."""
    files = sorted(p for p in Path(directory).rglob("*.md") if p.name.lower() != "readme.md")
    return [load_sop(p) for p in files]


def _parse_body(body: str, source: str) -> tuple[list[Section], list[Step]]:
    headings = list(H2.finditer(body))
    sections: list[Section] = []
    steps: list[Step] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        title = heading.group(1).strip()
        content = body[heading.end():end].strip()
        if title.lower() == PROCEDURE_SECTION:
            intro, steps = _parse_steps(content, source)
            if intro:
                sections.append(Section(title=title, text=intro))
        else:
            sections.append(Section(title=title, text=content))
    return sections, steps


def _parse_steps(content: str, source: str) -> tuple[str, list[Step]]:
    headings = list(STEP_HEADING.finditer(content))
    intro = content[: headings[0].start()].strip() if headings else content
    steps = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        step_id, title = heading.group(1), heading.group(2)
        fields, instruction = _parse_step_body(content[heading.end():end], f"{source}#{step_id}")
        try:
            steps.append(Step(step_id=step_id, title=title, instruction=instruction, **fields))
        except ValueError as exc:
            raise SopParseError(source, f"step {step_id}: {exc}") from exc
    return intro, steps


def _parse_step_body(block: str, where: str) -> tuple[dict[str, Any], str]:
    fields: dict[str, Any] = {}
    lines = block.strip().splitlines()
    position = 0
    for position, line in enumerate(lines):  # noqa: B007 - position is used after the loop
        match = STEP_FIELD.match(line.strip())
        if not match or match.group(1) not in STEP_FIELDS:
            break
        key, raw = match.groups()
        try:
            fields[key] = yaml.safe_load(raw) if raw else None
        except yaml.YAMLError as exc:
            raise SopParseError(where, f"field '{key}' is not valid YAML: {raw}") from exc
    else:
        position = len(lines)
    if fields.get("next") is not None and not isinstance(fields["next"], dict):
        raise SopParseError(where, "field 'next' must be a mapping like { up: S2, down: S3 }")
    fields["next"] = {str(k): str(v) for k, v in (fields.get("next") or {}).items()}
    return fields, "\n".join(lines[position:]).strip()
