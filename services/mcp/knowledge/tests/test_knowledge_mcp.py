"""End-to-end over MCP: client -> knowledge MCP server -> hybrid retriever -> store."""

from pathlib import Path

import httpx
import pytest

from knowledge_base import HashingEmbedder, HybridRetriever, MemoryStore, ingest_library
from knowledge_mcp import create_server
from poc_common.mcp_client import McpToolError, call_tool, list_tools
from poc_common.testing import run_app_in_thread
from sop_kit import load_library

LIBRARY = Path(__file__).resolve().parents[4] / "knowledge"
TOOLS = {
    "search_knowledge", "find_troubleshooting_sop", "get_sop_procedure", "get_sop_step",
    "get_next_sop_step",
}


@pytest.fixture(scope="module")
def base_url():
    store, embedder = MemoryStore(), HashingEmbedder()
    ingest_library(load_library(LIBRARY), store, embedder)
    retriever = HybridRetriever(store, embedder, min_similarity=0.2)
    with run_app_in_thread(create_server(retriever).streamable_http_app()) as url:
        yield url


@pytest.fixture
def mcp_url(base_url):
    return f"{base_url}/mcp"


async def test_all_tools_are_listed_and_read_only(mcp_url):
    tools = {t["name"]: t for t in await list_tools(mcp_url)}
    assert set(tools) == TOOLS
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools.values())


async def test_search_knowledge_returns_cited_passages(mcp_url):
    hits = await call_tool(mcp_url, "search_knowledge", {
        "query": "what does a red LOS light mean", "agent": "access-pon",
    })
    assert hits and {"citation", "text", "score", "doc_id"} <= set(hits[0])
    assert {h["doc_id"] for h in hits} <= {"SOP-PON-001", "SOP-PON-002", "KB-LED-001",
                                           "SOP-ESC-001"}


async def test_find_troubleshooting_sop(mcp_url):
    result = await call_tool(mcp_url, "find_troubleshooting_sop", {
        "symptom": "no internet, internet light orange, PPP session down", "agent": "inhome",
    })
    assert result["found"] is True
    assert result["candidates"][0]["doc_id"] == "SOP-IH-001"
    assert result["candidates"][0]["entry_step"] == "S1"


async def test_find_troubleshooting_sop_says_when_nothing_applies(mcp_url):
    result = await call_tool(mcp_url, "find_troubleshooting_sop", {
        "symptom": "chocolate cake recipe", "agent": "inhome",
    })
    assert result == {"found": False, "candidates": []}


async def test_walk_the_stuck_modem_procedure(mcp_url):
    entry = await call_tool(mcp_url, "get_sop_step", {"doc_id": "sop-ih-001", "step_id": "s1"})
    assert entry["tool"] == "network-diagnostics.get_ppp_session"

    step, path = "S1", []
    for outcome in ["down", "modem_stuck", "done", "up"]:
        result = await call_tool(mcp_url, "get_next_sop_step", {
            "doc_id": "SOP-IH-001", "step_id": step, "outcome": outcome,
        })
        step = result["next_step"]["step_id"]
        path.append((step, result["next_step"]["requires_consent"]))
    assert path == [("S2", False), ("S3", True), ("S4", False), ("S5", False)]


async def test_invalid_outcome_is_a_helpful_error(mcp_url):
    with pytest.raises(McpToolError, match="choose one of: up, down"):
        await call_tool(mcp_url, "get_next_sop_step", {
            "doc_id": "SOP-IH-001", "step_id": "S1", "outcome": "unknown",
        })


async def test_unknown_document_lists_the_known_ones(mcp_url):
    with pytest.raises(McpToolError, match="known documents: KB-LED-001"):
        await call_tool(mcp_url, "get_sop_procedure", {"doc_id": "SOP-XX-001"})


async def test_reference_documents_have_no_procedure(mcp_url):
    with pytest.raises(McpToolError, match="without steps"):
        await call_tool(mcp_url, "get_sop_procedure", {"doc_id": "KB-LED-001"})


async def test_get_sop_procedure_returns_every_step(mcp_url):
    procedure = await call_tool(mcp_url, "get_sop_procedure", {"doc_id": "SOP-PON-001"})
    assert procedure["entry_step"] == "S1"
    consent = [s["step_id"] for s in procedure["steps"] if s["requires_consent"]]
    assert consent == ["S6"]


def test_http_routes(base_url):
    assert httpx.get(f"{base_url}/health").json() == {
        "status": "ok", "service": "knowledge-mcp", "documents": 10,
    }
    assert len(httpx.get(f"{base_url}/v1/documents").json()) == 10
    hits = httpx.get(f"{base_url}/v1/search", params={"q": "reset wifi", "agent": "inhome"}).json()
    assert hits[0]["doc_id"] == "SOP-IH-002"
    assert httpx.get(f"{base_url}/v1/search").status_code == 400
