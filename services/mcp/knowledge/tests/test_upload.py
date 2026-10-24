"""Document upload: converter output becomes a searchable, citable reference document.

A fake converter stands in for the Docling container (its HTTP client is tested separately).
"""

from pathlib import Path

import httpx
import pytest

from knowledge_base import (
    ConversionError,
    HashingEmbedder,
    HybridRetriever,
    MemoryStore,
    ingest_library,
)
from knowledge_mcp import server as server_module
from knowledge_mcp.server import create_server
from poc_common.testing import run_app_in_thread
from sop_kit import load_library

LIBRARY = Path(__file__).resolve().parents[4] / "knowledge"
GUIDE = (
    "# FibreLink ONT-100 quick guide\n\n"
    "## Factory reset\n\nHold the recessed reset button for 12 seconds with a paperclip.\n\n"
    "## Optical levels\n\n| Level | Meaning |\n|---|---|\n| below -27 dBm | Signal too weak |\n"
)


class FakeConverter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.markdown = GUIDE
        self.error: str | None = None

    def convert(self, filename: str, content: bytes) -> str:
        self.calls.append((filename, content))
        if self.error:
            raise ConversionError(self.error)
        return self.markdown


@pytest.fixture
def knowledge():
    store, embedder = MemoryStore(), HashingEmbedder()
    ingest_library(load_library(LIBRARY), store, embedder)
    retriever = HybridRetriever(store, embedder, min_similarity=0.2)
    converter = FakeConverter()
    app = create_server(retriever, converter=converter).streamable_http_app()
    with run_app_in_thread(app) as base:
        yield base, converter, store


def upload(base, name="guide.docx", content=b"binary", **fields):
    return httpx.post(f"{base}/v1/documents/upload", files={"file": (name, content)},
                      data=fields, timeout=30)


def test_a_docx_is_converted_ingested_and_found_by_the_right_agents(knowledge):
    base, converter, _ = knowledge

    response = upload(base, content=b"PK-docx", agents="access-pon")

    body = response.json()
    assert response.status_code == 201
    assert body["doc_id"] == "KB-UPL-001" and body["version"] == "1.0"
    assert body["title"] == "FibreLink ONT-100 quick guide" and body["kind"] == "reference"
    assert body["sections"] == 2 and body["chunks"] == 3 and body["converted_by"] == "docling"
    assert converter.calls == [("guide.docx", b"PK-docx")]

    hits = httpx.get(f"{base}/v1/search", params={
        "q": "how long do I hold the reset button", "agent": "access-pon", "top_k": 3}).json()
    assert hits[0]["doc_id"] == "KB-UPL-001" and "12 seconds" in hits[0]["text"]
    assert "KB-UPL-001 v1.0" in hits[0]["citation"]
    other = httpx.get(f"{base}/v1/search", params={
        "q": "how long do I hold the reset button", "agent": "inhome", "top_k": 3}).json()
    assert "KB-UPL-001" not in [h["doc_id"] for h in other], "scoped to the chosen agents"

    listed = {d["doc_id"]: d for d in httpx.get(f"{base}/v1/documents").json()}
    assert listed["KB-UPL-001"]["source"] == "upload:guide.docx"
    assert listed["KB-UPL-001"]["chunks"] == 3


def test_the_title_can_be_chosen_and_markdown_needs_no_converter(knowledge):
    base, converter, _ = knowledge

    body = upload(base, "notes.md", GUIDE.encode(), title="ONT cheat sheet").json()

    assert body["title"] == "ONT cheat sheet" and body["converted_by"] == "as-is"
    assert converter.calls == []


def test_uploading_the_same_file_again_makes_a_new_version_of_the_same_document(knowledge):
    base, converter, store = knowledge
    upload(base)
    converter.markdown = GUIDE.replace("12 seconds", "15 seconds")

    body = upload(base).json()

    assert (body["doc_id"], body["version"]) == ("KB-UPL-001", "1.1")
    assert [d["version"] for d in store.list_documents() if d["doc_id"] == "KB-UPL-001"] == ["1.1"]
    assert upload(base, "other.pdf").json()["doc_id"] == "KB-UPL-002"


@pytest.mark.parametrize("name, status", [("virus.exe", 415), ("noextension", 415)])
def test_other_file_types_are_refused(knowledge, name, status):
    base, converter, _ = knowledge
    response = upload(base, name)
    assert response.status_code == status and "unsupported file type" in response.json()["detail"]
    assert converter.calls == []


def test_a_missing_or_oversized_file_is_refused(knowledge, monkeypatch):
    base, _, _ = knowledge
    assert httpx.post(f"{base}/v1/documents/upload", data={"title": "x"}).status_code == 400
    monkeypatch.setattr(server_module, "MAX_UPLOAD_BYTES", 10)
    response = upload(base, content=b"x" * 11)
    assert response.status_code == 413


def test_a_conversion_failure_is_reported_and_nothing_is_stored(knowledge):
    base, converter, store = knowledge
    converter.error = "the document processor is not reachable (ConnectError)"
    before = len(store.list_documents())

    response = upload(base)

    assert response.status_code == 502 and "not reachable" in response.json()["detail"]
    assert len(store.list_documents()) == before


def test_text_without_content_or_with_unknown_agents_is_unprocessable(knowledge):
    base, converter, _ = knowledge
    converter.markdown = "   "
    assert upload(base).status_code == 422
    converter.markdown = GUIDE
    response = upload(base, agents="nobody")
    assert response.status_code == 422 and "agent_scope" in response.json()["detail"]


def test_only_uploaded_documents_can_be_removed(knowledge):
    base, _, store = knowledge
    upload(base)

    assert httpx.delete(f"{base}/v1/documents/kb-upl-001").json() == {"deleted": "KB-UPL-001"}
    assert "KB-UPL-001" not in [d["doc_id"] for d in store.list_documents()]
    assert httpx.delete(f"{base}/v1/documents/KB-UPL-001").status_code == 404
    refused = httpx.delete(f"{base}/v1/documents/SOP-IH-001")
    assert refused.status_code == 403 and "SOP-IH-001" in [
        d["doc_id"] for d in store.list_documents()]


def test_without_a_document_processor_only_markdown_can_be_added():
    store, embedder = MemoryStore(), HashingEmbedder()
    ingest_library(load_library(LIBRARY), store, embedder)
    app = create_server(HybridRetriever(store, embedder)).streamable_http_app()
    with run_app_in_thread(app) as base:
        assert upload(base, "a.docx").status_code == 502
        assert upload(base, "a.md", GUIDE.encode()).status_code == 201


def test_agents_cannot_upload_through_mcp(knowledge):
    """Uploading is a plain HTTP route, not an MCP tool."""
    import asyncio

    from poc_common.mcp_client import list_tools

    base, _, _ = knowledge
    names = {t["name"] for t in asyncio.run(list_tools(f"{base}/mcp"))}
    assert not {n for n in names if "upload" in n or "delete" in n or "ingest" in n}