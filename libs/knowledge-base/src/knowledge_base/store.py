"""Knowledge store interface plus an in-memory implementation (for tests and laptops without
Postgres). `pg_store.PgVectorStore` implements the same interface on Postgres + pgvector."""

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from knowledge_base.embedding import STOPWORDS, cosine
from sop_kit import Chunk, SopDocument

WORD = re.compile(r"[a-z0-9]+")


def keyword_terms(text: str, *, stem: bool = True) -> list[str]:
    """Lower-cased words without stop words, with a light plural stemmer ("lights" -> "light").

    Pass `stem=False` when the database stems the words itself (Postgres full-text search).
    """
    terms = []
    for word in WORD.findall(text.lower()):
        if len(word) < 2 or word in STOPWORDS:
            continue
        if stem and len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        terms.append(word)
    return terms


@dataclass(frozen=True)
class Filters:
    agent: str | None = None
    carrier_tech_type: str | None = None
    kind: str | None = None
    chunk_types: tuple[str, ...] | None = None
    doc_id: str | None = None

    def matches(self, chunk_type: str, metadata: dict[str, Any]) -> bool:
        if self.agent and self.agent not in metadata.get("agent_scope", []):
            return False
        techs = [t.upper() for t in metadata.get("carrier_tech_types", [])]
        if self.carrier_tech_type and techs and self.carrier_tech_type.upper() not in techs:
            return False
        if self.kind and metadata.get("kind") != self.kind:
            return False
        if self.chunk_types and chunk_type not in self.chunk_types:
            return False
        return not (self.doc_id and metadata.get("doc_id") != self.doc_id)


@dataclass
class StoredChunk:
    chunk_id: str
    doc_id: str
    version: str
    chunk_type: str
    section_path: list[str]
    step_id: str | None
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgeStore(ABC):
    @abstractmethod
    def ensure_schema(self) -> None: ...

    @abstractmethod
    def checksums(self) -> dict[str, str]:
        """doc_id -> checksum of every stored document."""

    @abstractmethod
    def upsert(self, doc: SopDocument, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Replace the document and all its chunks atomically."""

    @abstractmethod
    def delete(self, doc_ids: list[str]) -> None: ...

    @abstractmethod
    def keyword_search(self, query: str, filters: Filters, limit: int) -> list[tuple[str, float]]:
        """(chunk_id, score), best first; only chunks sharing at least one term."""

    @abstractmethod
    def vector_search(
        self, embedding: list[float], filters: Filters, limit: int
    ) -> list[tuple[str, float]]:
        """(chunk_id, cosine similarity), best first."""

    @abstractmethod
    def get_chunks(self, chunk_ids: list[str]) -> dict[str, StoredChunk]: ...

    @abstractmethod
    def get_document(self, doc_id: str) -> SopDocument | None: ...

    @abstractmethod
    def list_documents(self) -> list[dict[str, Any]]: ...


class MemoryStore(KnowledgeStore):
    K1, B = 1.2, 0.75

    def __init__(self) -> None:
        self._docs: dict[str, SopDocument] = {}
        self._chunks: dict[str, StoredChunk] = {}
        self._vectors: dict[str, list[float]] = {}
        self._terms: dict[str, Counter] = {}

    def ensure_schema(self) -> None:
        return None

    def checksums(self) -> dict[str, str]:
        return {doc_id: doc.checksum for doc_id, doc in self._docs.items()}

    def upsert(self, doc: SopDocument, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        self.delete([doc.doc_id])
        self._docs[doc.doc_id] = doc
        for chunk, vector in zip(chunks, embeddings, strict=True):
            self._chunks[chunk.chunk_id] = StoredChunk(
                chunk.chunk_id, chunk.doc_id, chunk.version, chunk.chunk_type,
                chunk.section_path, chunk.step_id, chunk.text, chunk.metadata,
            )
            self._vectors[chunk.chunk_id] = vector
            self._terms[chunk.chunk_id] = Counter(keyword_terms(chunk.text))

    def delete(self, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            self._docs.pop(doc_id, None)
            for chunk_id in [c for c, ch in self._chunks.items() if ch.doc_id == doc_id]:
                del self._chunks[chunk_id], self._vectors[chunk_id], self._terms[chunk_id]

    def _candidates(self, filters: Filters) -> list[str]:
        return [cid for cid, c in self._chunks.items() if filters.matches(c.chunk_type, c.metadata)]

    def keyword_search(self, query: str, filters: Filters, limit: int) -> list[tuple[str, float]]:
        query_terms = set(keyword_terms(query))
        candidates = self._candidates(filters)
        if not query_terms or not candidates:
            return []
        total = len(self._terms)
        average = sum(sum(t.values()) for t in self._terms.values()) / total
        scores = []
        for chunk_id in candidates:
            terms = self._terms[chunk_id]
            length, score = sum(terms.values()), 0.0
            for term in query_terms & terms.keys():
                df = sum(1 for t in self._terms.values() if term in t)
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                tf = terms[term]
                score += idf * tf * (self.K1 + 1) / (
                    tf + self.K1 * (1 - self.B + self.B * length / average)
                )
            if score > 0:
                scores.append((chunk_id, score))
        return sorted(scores, key=lambda s: s[1], reverse=True)[:limit]

    def vector_search(
        self, embedding: list[float], filters: Filters, limit: int
    ) -> list[tuple[str, float]]:
        scores = [(cid, cosine(embedding, self._vectors[cid])) for cid in self._candidates(filters)]
        return sorted(scores, key=lambda s: s[1], reverse=True)[:limit]

    def get_chunks(self, chunk_ids: list[str]) -> dict[str, StoredChunk]:
        return {cid: self._chunks[cid] for cid in chunk_ids if cid in self._chunks}

    def get_document(self, doc_id: str) -> SopDocument | None:
        return self._docs.get(doc_id)

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {"doc_id": d.doc_id, "title": d.title, "version": d.version, "kind": d.kind.value,
             "source": d.source, "agent_scope": d.agent_scope, "chunks": sum(
                 1 for c in self._chunks.values() if c.doc_id == d.doc_id)}
            for d in sorted(self._docs.values(), key=lambda d: d.doc_id)
        ]
