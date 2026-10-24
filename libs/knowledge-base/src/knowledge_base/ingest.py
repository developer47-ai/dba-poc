"""Load the knowledge library into the store: validate -> chunk -> embed -> upsert.

Idempotent: unchanged documents (same checksum) are skipped, changed ones are replaced
atomically, and documents that disappeared or are no longer `active` are removed.

    python -m knowledge_base.ingest   # uses KNOWLEDGE_DIR, DATABASE_URL and the Ollama embedder
"""

import logging
import sys
import time
from dataclasses import dataclass, field

from knowledge_base.documents import UPLOAD_PREFIX
from knowledge_base.embedding import Embedder
from knowledge_base.store import KnowledgeStore
from sop_kit import SopDocument, chunk_document, load_library, validate_library
from sop_kit.models import DocStatus

logger = logging.getLogger(__name__)


class LibraryInvalidError(ValueError):
    def __init__(self, problems: dict[str, list[str]]) -> None:
        details = "; ".join(f"{doc}: {', '.join(p)}" for doc, p in problems.items())
        super().__init__(f"knowledge library is invalid: {details}")
        self.problems = problems


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    not_active: list[str] = field(default_factory=list)
    chunks: int = 0

    def summary(self) -> str:
        return (
            f"added={len(self.added)} updated={len(self.updated)} "
            f"unchanged={len(self.unchanged)} removed={len(self.removed)} "
            f"not_active={len(self.not_active)} chunks_embedded={self.chunks}"
        )


def ingest_library(
    docs: list[SopDocument], store: KnowledgeStore, embedder: Embedder
) -> IngestReport:
    problems = validate_library(docs)
    if problems:
        raise LibraryInvalidError(problems)

    store.ensure_schema()
    existing = store.checksums()
    report = IngestReport()
    active = [d for d in docs if d.status is DocStatus.ACTIVE]
    report.not_active = sorted(d.doc_id for d in docs if d.status is not DocStatus.ACTIVE)

    for doc in active:
        if existing.get(doc.doc_id) == doc.checksum:
            report.unchanged.append(doc.doc_id)
            continue
        chunks = chunk_document(doc)
        vectors = embedder.embed_documents([c.text for c in chunks])
        wrong = [len(v) for v in vectors if len(v) != embedder.dimensions]
        if wrong:
            raise ValueError(
                f"embedding size {wrong[0]} does not match the expected {embedder.dimensions}"
            )
        store.upsert(doc, chunks, vectors)
        report.chunks += len(chunks)
        (report.updated if doc.doc_id in existing else report.added).append(doc.doc_id)
        logger.info("ingested %s v%s (%d chunks)", doc.doc_id, doc.version, len(chunks))

    # Uploaded documents are not part of the library; only library documents can be removed here.
    uploads = {d["doc_id"] for d in store.list_documents()
               if d.get("source", "").startswith(UPLOAD_PREFIX)}
    report.removed = sorted(set(existing) - {d.doc_id for d in active} - uploads)
    store.delete(report.removed)
    return report


def main() -> int:
    from knowledge_base.embedding import OllamaEmbedder
    from knowledge_base.pg_store import PgVectorStore
    from poc_common.config import get_config
    from poc_common.logging import setup_logging

    config = get_config()
    setup_logging("kb-ingestion", config.log_level, config.log_json)
    started = time.monotonic()
    try:
        docs = load_library(config.knowledge_dir)
        store = PgVectorStore(
            config.database_url, schema=config.kb_schema, dimensions=config.embed_dimensions
        )
        try:
            report = ingest_library(docs, store, OllamaEmbedder(config))
        finally:
            store.close()
    except LibraryInvalidError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "knowledge ingestion done in %.1fs: %s", time.monotonic() - started, report.summary()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
