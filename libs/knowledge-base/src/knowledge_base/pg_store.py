"""Postgres + pgvector implementation of the knowledge store.

- `document`: one row per active document; the full parsed SOP is kept as JSON so procedures can
  be navigated step by step.
- `chunk`: one row per search chunk with its bge-m3 vector (HNSW index, cosine distance) and a
  generated English full-text vector (GIN index) for keyword search.
"""

import re
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from knowledge_base.store import Filters, KnowledgeStore, StoredChunk, keyword_terms
from sop_kit import Chunk, SopDocument

SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


class PgVectorStore(KnowledgeStore):
    def __init__(
        self, dsn: str, *, schema: str = "kb", dimensions: int = 1024, max_size: int = 4
    ) -> None:
        if not SCHEMA_NAME.match(schema):
            raise ValueError(f"invalid schema name: {schema!r}")
        self.schema, self.dimensions = schema, dimensions
        # The extension must exist before connections can register the vector type.
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=max_size, configure=register_vector,
            kwargs={"row_factory": dict_row}, open=True,
        )

    def close(self) -> None:
        self._pool.close()

    def ensure_schema(self) -> None:
        s, d = self.schema, self.dimensions
        statements = [
            f"CREATE SCHEMA IF NOT EXISTS {s}",
            f"""CREATE TABLE IF NOT EXISTS {s}.document (
                doc_id      text PRIMARY KEY,
                version     text NOT NULL,
                title       text NOT NULL,
                kind        text NOT NULL,
                checksum    text NOT NULL,
                source      text NOT NULL DEFAULT '',
                metadata    jsonb NOT NULL,
                body        jsonb NOT NULL,
                ingested_at timestamptz NOT NULL DEFAULT now())""",
            f"""CREATE TABLE IF NOT EXISTS {s}.chunk (
                chunk_id     text PRIMARY KEY,
                doc_id       text NOT NULL REFERENCES {s}.document(doc_id) ON DELETE CASCADE,
                version      text NOT NULL,
                chunk_type   text NOT NULL,
                section_path text[] NOT NULL,
                step_id      text,
                text         text NOT NULL,
                metadata     jsonb NOT NULL,
                embedding    vector({d}) NOT NULL,
                tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED)""",
            f"CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw ON {s}.chunk "
            "USING hnsw (embedding vector_cosine_ops)",
            f"CREATE INDEX IF NOT EXISTS chunk_tsv_gin ON {s}.chunk USING gin (tsv)",
            f"CREATE INDEX IF NOT EXISTS chunk_doc ON {s}.chunk (doc_id)",
        ]
        with self._pool.connection() as conn:
            for statement in statements:
                conn.execute(statement)

    def drop_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def checksums(self) -> dict[str, str]:
        with self._pool.connection() as conn:
            rows = conn.execute(f"SELECT doc_id, checksum FROM {self.schema}.document").fetchall()
        return {r["doc_id"]: r["checksum"] for r in rows}

    def upsert(self, doc: SopDocument, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        s = self.schema
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(f"DELETE FROM {s}.document WHERE doc_id = %s", (doc.doc_id,))
            conn.execute(
                f"INSERT INTO {s}.document (doc_id, version, title, kind, checksum, source, "
                "metadata, body) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (doc.doc_id, doc.version, doc.title, doc.kind.value, doc.checksum, doc.source,
                 Jsonb(doc.metadata()), Jsonb(doc.model_dump(mode="json"))),
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    f"INSERT INTO {s}.chunk (chunk_id, doc_id, version, chunk_type, section_path, "
                    "step_id, text, metadata, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (c.chunk_id, c.doc_id, c.version, c.chunk_type, c.section_path,
                         c.step_id, c.text, Jsonb(c.metadata), Vector(vector))
                        for c, vector in zip(chunks, embeddings, strict=True)
                    ],
                )

    def delete(self, doc_ids: list[str]) -> None:
        if doc_ids:
            with self._pool.connection() as conn:
                conn.execute(
                    f"DELETE FROM {self.schema}.document WHERE doc_id = ANY(%s)", (doc_ids,)
                )

    @staticmethod
    def _where(filters: Filters) -> tuple[str, list[Any]]:
        clauses, params = ["TRUE"], []
        if filters.agent:
            clauses.append("metadata->'agent_scope' ? %s")
            params.append(filters.agent)
        if filters.carrier_tech_type:
            clauses.append(
                "(jsonb_array_length(metadata->'carrier_tech_types') = 0 OR EXISTS ("
                "SELECT 1 FROM jsonb_array_elements_text(metadata->'carrier_tech_types') t "
                "WHERE upper(t) = upper(%s)))"
            )
            params.append(filters.carrier_tech_type)
        if filters.kind:
            clauses.append("metadata->>'kind' = %s")
            params.append(filters.kind)
        if filters.chunk_types:
            clauses.append("chunk_type = ANY(%s)")
            params.append(list(filters.chunk_types))
        if filters.doc_id:
            clauses.append("doc_id = %s")
            params.append(filters.doc_id)
        return " AND ".join(clauses), params

    def keyword_search(self, query: str, filters: Filters, limit: int) -> list[tuple[str, float]]:
        terms = sorted(set(keyword_terms(query, stem=False)))
        if not terms:
            return []
        where, params = self._where(filters)
        sql = (
            f"SELECT chunk_id, ts_rank_cd(tsv, q) AS score "
            f"FROM {self.schema}.chunk, to_tsquery('english', %s) AS q "
            f"WHERE tsv @@ q AND {where} ORDER BY score DESC, chunk_id LIMIT %s"
        )
        with self._pool.connection() as conn:
            rows = conn.execute(sql, [" | ".join(terms), *params, limit]).fetchall()
        return [(r["chunk_id"], float(r["score"])) for r in rows]

    def vector_search(
        self, embedding: list[float], filters: Filters, limit: int
    ) -> list[tuple[str, float]]:
        where, params = self._where(filters)
        vector = Vector(embedding)
        sql = (
            f"SELECT chunk_id, 1 - (embedding <=> %s) AS similarity FROM {self.schema}.chunk "
            f"WHERE {where} ORDER BY embedding <=> %s, chunk_id LIMIT %s"
        )
        with self._pool.connection() as conn:
            rows = conn.execute(sql, [vector, *params, vector, limit]).fetchall()
        return [(r["chunk_id"], float(r["similarity"])) for r in rows]

    def get_chunks(self, chunk_ids: list[str]) -> dict[str, StoredChunk]:
        if not chunk_ids:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT chunk_id, doc_id, version, chunk_type, section_path, step_id, text, "
                f"metadata FROM {self.schema}.chunk WHERE chunk_id = ANY(%s)", (chunk_ids,),
            ).fetchall()
        return {r["chunk_id"]: StoredChunk(**r) for r in rows}

    def get_document(self, doc_id: str) -> SopDocument | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT body FROM {self.schema}.document WHERE doc_id = %s", (doc_id,)
            ).fetchone()
        return SopDocument.model_validate(row["body"]) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT d.doc_id, d.title, d.version, d.kind, "
                f"d.metadata->'agent_scope' AS agent_scope, count(c.chunk_id) AS chunks "
                f"FROM {self.schema}.document d LEFT JOIN {self.schema}.chunk c USING (doc_id) "
                f"GROUP BY d.doc_id ORDER BY d.doc_id"
            ).fetchall()
        return [dict(r) for r in rows]
