import os
import uuid
from pathlib import Path

import pytest

from knowledge_base import HashingEmbedder, HybridRetriever, MemoryStore, ingest_library
from poc_common.config import Config
from sop_kit import load_library

LIBRARY = Path(__file__).resolve().parents[3] / "knowledge"
RUN_PG = os.getenv("RUN_PG_TESTS") == "1"


@pytest.fixture(scope="session")
def library():
    return load_library(LIBRARY)


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dimensions=1024)


@pytest.fixture(params=["memory", pytest.param("pg", marks=pytest.mark.pg)])
def store(request):
    """The same contract tests run against the in-memory store and Postgres + pgvector."""
    if request.param == "memory":
        yield MemoryStore()
        return
    if not RUN_PG:
        pytest.skip("needs Postgres with pgvector; set RUN_PG_TESTS=1 and DATABASE_URL")
    from knowledge_base.pg_store import PgVectorStore

    pg = PgVectorStore(Config().database_url, schema=f"kb_test_{uuid.uuid4().hex[:8]}")
    try:
        yield pg
    finally:
        pg.drop_schema()
        pg.close()


@pytest.fixture
def loaded(store, library, embedder):
    ingest_library(library, store, embedder)
    return store


@pytest.fixture
def retriever(loaded, embedder) -> HybridRetriever:
    # The hashing stand-in gives lower similarities than bge-m3, so its gate is lower too.
    return HybridRetriever(loaded, embedder, min_similarity=0.2)
