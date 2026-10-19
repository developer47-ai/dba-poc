"""Hybrid retrieval: keyword (BM25 / full-text) + vector search, merged with Reciprocal Rank Fusion.

Keyword search finds exact terms ("LOS", "PPP"); vector search finds meaning ("no light from the
network"). RRF adds 1 / (60 + rank) from each list, so a chunk ranked well by both wins. A
relevance gate keeps only chunks that matched a keyword or are similar enough in meaning, which
lets the system answer "no matching SOP" instead of returning noise.
"""

from dataclasses import dataclass
from typing import Any

from knowledge_base.embedding import Embedder
from knowledge_base.store import Filters, KnowledgeStore, StoredChunk

RRF_K = 60


@dataclass
class SearchHit:
    chunk: StoredChunk
    score: float
    similarity: float
    keyword_match: bool

    @property
    def citation(self) -> str:
        step = f" › {self.chunk.step_id}" if self.chunk.step_id else ""
        return f"{self.chunk.doc_id} v{self.chunk.version}{step}"

    def as_dict(self, max_chars: int = 1200) -> dict[str, Any]:
        text = self.chunk.text
        return {
            "doc_id": self.chunk.doc_id,
            "title": self.chunk.metadata.get("title", ""),
            "kind": self.chunk.metadata.get("kind", ""),
            "section": " › ".join(self.chunk.section_path),
            "step_id": self.chunk.step_id,
            "citation": self.citation,
            "score": round(self.score, 5),
            "similarity": round(self.similarity, 3),
            "keyword_match": self.keyword_match,
            "text": text if len(text) <= max_chars else text[: max_chars - 1] + "…",
        }


class HybridRetriever:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: Embedder,
        *,
        min_similarity: float = 0.45,
        candidates: int = 20,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.min_similarity = min_similarity
        self.candidates = candidates

    def search(self, query: str, filters: Filters | None = None, top_k: int = 4) -> list[SearchHit]:
        filters = filters or Filters()
        keyword = self.store.keyword_search(query, filters, self.candidates)
        vector = self.store.vector_search(
            self.embedder.embed_query(query), filters, self.candidates
        )
        similarity = dict(vector)
        keyword_ids = {chunk_id for chunk_id, _ in keyword}

        fused: dict[str, float] = {}
        for ranking in (keyword, vector):
            for rank, (chunk_id, _) in enumerate(ranking, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)

        relevant = [
            chunk_id for chunk_id in fused
            if chunk_id in keyword_ids or similarity.get(chunk_id, 0.0) >= self.min_similarity
        ]
        ranked = sorted(relevant, key=lambda c: fused[c], reverse=True)[:top_k]
        chunks = self.store.get_chunks(ranked)
        return [
            SearchHit(chunks[c], fused[c], similarity.get(c, 0.0), c in keyword_ids)
            for c in ranked if c in chunks
        ]

    def find_procedures(
        self, symptom: str, filters: Filters | None = None, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Rank whole procedures (not chunks) for a described problem."""
        base = filters or Filters()
        procedure_filters = Filters(
            agent=base.agent, carrier_tech_type=base.carrier_tech_type, kind="procedure",
        )
        hits = self.search(symptom, procedure_filters, top_k=self.candidates)
        best: dict[str, SearchHit] = {}
        for hit in hits:
            doc_id = hit.chunk.doc_id
            if doc_id not in best or hit.score > best[doc_id].score:
                best[doc_id] = hit
        ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)[:top_k]
        results = []
        for hit in ranked:
            doc = self.store.get_document(hit.chunk.doc_id)
            results.append({
                "doc_id": hit.chunk.doc_id,
                "title": hit.chunk.metadata.get("title", ""),
                "version": hit.chunk.version,
                "entry_step": doc.entry_step if doc else None,
                "matched": hit.citation,
                "score": round(hit.score, 5),
                "similarity": round(hit.similarity, 3),
                "symptoms": hit.chunk.metadata.get("symptoms", []),
            })
        return results
