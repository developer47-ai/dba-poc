"""Knowledge base: storage, ingestion and hybrid retrieval for SOPs and reference documents."""

from knowledge_base.embedding import Embedder, HashingEmbedder, OllamaEmbedder, cosine
from knowledge_base.ingest import IngestReport, LibraryInvalidError, ingest_library
from knowledge_base.retriever import HybridRetriever, SearchHit
from knowledge_base.store import Filters, KnowledgeStore, MemoryStore, StoredChunk

__all__ = [
    "Embedder", "Filters", "HashingEmbedder", "HybridRetriever", "IngestReport",
    "KnowledgeStore", "LibraryInvalidError", "MemoryStore", "OllamaEmbedder", "SearchHit",
    "StoredChunk", "cosine", "ingest_library",
]
