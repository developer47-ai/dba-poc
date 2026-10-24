"""Turn converted documents (Markdown from Docling) into searchable knowledge.

An upload becomes a *reference* document (`KB-UPL-nnn`): searchable and citable, but never a
procedure, so an unreviewed file can inform an answer yet can never tell an agent which action
to run. Uploads are marked with `source = "upload:<filename>"`; the library sync leaves them alone.
"""

import hashlib
import re
from datetime import date

from knowledge_base.embedding import Embedder
from knowledge_base.store import KnowledgeStore
from sop_kit import SopDocument, chunk_document
from sop_kit.catalog import AGENTS
from sop_kit.models import DocKind, DocStatus, Section
from sop_kit.validator import validate

UPLOAD_PREFIX = "upload:"
UPLOAD_ID = "KB-UPL-{:03d}"
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
MAX_TITLE = 120


class DocumentInvalidError(ValueError):
    pass


def split_sections(markdown: str) -> tuple[str | None, list[Section]]:
    """The document's title (a leading `#` heading) and its sections.

    Docling uses several heading levels; the chunker works on flat sections, so every heading
    starts a section titled with its path ("Chapter › Part"). Text before the first heading
    becomes an "Overview" section. Tables stay inside their section.
    """
    text = COMMENT.sub("", markdown)
    title: str | None = None
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    buffer: list[str] = []
    path = ["Overview"]
    in_fence = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(Section(title=" › ".join(path)[:MAX_TITLE], text=body))
        buffer.clear()

    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if not match:
            buffer.append(line)
            continue
        level, name = len(match.group(1)), match.group(2).strip()
        if level == 1 and title is None and not sections and not "".join(buffer).strip():
            title = name
            continue
        flush()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, name))
        path = [n for _, n in stack]
    flush()
    return title, sections


def plan_upload(existing: list[dict], filename: str) -> tuple[str, str]:
    """(doc_id, version): a new id, or the same file again -> same id, next minor version."""
    source = f"{UPLOAD_PREFIX}{filename}"
    for doc in existing:
        if doc.get("source") == source:
            return doc["doc_id"], _next_minor(doc["version"])
    used = {doc["doc_id"] for doc in existing}
    for number in range(1, 1000):
        if UPLOAD_ID.format(number) not in used:
            return UPLOAD_ID.format(number), "1.0"
    raise DocumentInvalidError("too many uploaded documents")


def _next_minor(version: str) -> str:
    major, _, minor = version.partition(".")
    return f"{major}.{int(minor) + 1}" if major.isdigit() and minor.isdigit() else "1.1"


def build_reference(
    markdown: str, *, doc_id: str, version: str, filename: str, title: str | None = None,
    agents: list[str] | None = None, owner: str = "Uploaded document",
) -> SopDocument:
    guessed, sections = split_sections(markdown)
    stem = re.sub(r"[_-]+", " ", filename.rsplit(".", 1)[0])
    name = (title or guessed or stem).strip()
    doc = SopDocument(
        doc_id=doc_id, title=name[:MAX_TITLE], version=version, status=DocStatus.ACTIVE,
        kind=DocKind.REFERENCE, owner=owner, effective_from=date.today().isoformat(),
        agent_scope=agents or sorted(AGENTS), sections=sections,
        source=f"{UPLOAD_PREFIX}{filename}",
        checksum=hashlib.sha256(f"{doc_id}\n{version}\n{markdown}".encode()).hexdigest(),
    )
    problems = validate(doc)
    if problems:
        raise DocumentInvalidError("; ".join(problems))
    return doc


def ingest_document(doc: SopDocument, store: KnowledgeStore, embedder: Embedder) -> int:
    """Chunk, embed and store one document (replacing an earlier version); returns the chunks."""
    store.ensure_schema()
    chunks = chunk_document(doc)
    vectors = embedder.embed_documents([c.text for c in chunks])
    if any(len(v) != embedder.dimensions for v in vectors):
        raise ValueError("embedding size does not match the expected dimensions")
    store.upsert(doc, chunks, vectors)
    return len(chunks)
