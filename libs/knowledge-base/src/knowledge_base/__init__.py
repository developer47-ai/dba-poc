"""Knowledge base: storage, ingestion and hybrid retrieval for SOPs and reference documents."""

from knowledge_base.docling import ConversionError, DoclingClient
from knowledge_base.documents import (
    DocumentInvalidError,
    build_reference,
    ingest_document,
    plan_upload,
    split_sections,
)
from knowledge_base.embedding import Embedder, HashingEmbedder, OllamaEmbedder, cosine
from knowledge_base.ingest import IngestReport, LibraryInvalidError, ingest_library
from knowledge_base.retriever import HybridRetriever, SearchHit
from knowledge_base.store import Filters, KnowledgeStore, MemoryStore, StoredChunk

__all__ = [
    "ConversionError", "DocumentInvalidError", "DoclingClient", "Embedder", "Filters",
    "HashingEmbedder", "HybridRetriever", "IngestReport", "KnowledgeStore",
    "LibraryInvalidError", "MemoryStore", "OllamaEmbedder", "SearchHit", "StoredChunk",
    "build_reference", "cosine", "ingest_document", "ingest_library", "plan_upload",
    "split_sections",
]
