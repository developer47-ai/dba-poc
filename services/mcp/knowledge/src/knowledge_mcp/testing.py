"""Test helper: a knowledge MCP server over the real library, without Postgres or Ollama."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from knowledge_base import HashingEmbedder, HybridRetriever, MemoryStore, ingest_library
from knowledge_mcp.server import create_server
from poc_common.testing import run_app_in_thread
from sop_kit import load_library


@contextmanager
def run_test_knowledge_server(library_dir: str | Path) -> Iterator[str]:
    """Serve the knowledge MCP over an in-memory store; yields the MCP URL (…/mcp)."""
    store, embedder = MemoryStore(), HashingEmbedder()
    ingest_library(load_library(library_dir), store, embedder)
    retriever = HybridRetriever(store, embedder, min_similarity=0.2)
    with run_app_in_thread(create_server(retriever).streamable_http_app()) as base_url:
        yield f"{base_url}/mcp"
