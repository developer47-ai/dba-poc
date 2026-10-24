"""Knowledge MCP server: hybrid search over the SOP library and deterministic SOP navigation.

All tools are read-only. Agents use `find_troubleshooting_sop` to pick a procedure, then walk it
with `get_next_sop_step`, reporting the outcome they observed at each step; the server (not the
model) decides which step comes next.
"""

import logging
import os
import threading
import time
from typing import Any, Protocol

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from knowledge_base import (
    ConversionError,
    DocumentInvalidError,
    Filters,
    HybridRetriever,
    build_reference,
    ingest_document,
    plan_upload,
)
from knowledge_base.docling import SUPPORTED
from knowledge_base.documents import UPLOAD_PREFIX
from poc_common.config import get_config
from poc_common.logging import setup_logging
from sop_kit import NavigationError, SopDocument, get_step, next_step, procedure_view

logger = logging.getLogger(__name__)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class Converter(Protocol):
    def convert(self, filename: str, content: bytes) -> str: ...


def create_server(
    retriever: HybridRetriever,
    *,
    host: str = "127.0.0.1",
    port: int = 8105,
    converter: Converter | None = None,
) -> FastMCP:
    store = retriever.store
    # Binding to 0.0.0.0 (containers) also disables FastMCP's localhost-only Host header check.
    server = FastMCP("knowledge", host=host, port=port)

    def document(doc_id: str) -> SopDocument:
        doc = store.get_document(doc_id.strip().upper())
        if doc is None:
            known = ", ".join(d["doc_id"] for d in store.list_documents())
            raise ValueError(f"unknown document '{doc_id}'; known documents: {known}")
        return doc

    def run(function, *args):
        # Store and embedder calls block (database, HTTP); keep the event loop free.
        return anyio.to_thread.run_sync(function, *args)

    @server.tool(annotations=READ_ONLY)
    async def search_knowledge(
        query: str,
        agent: str | None = None,
        carrier_tech_type: str | None = None,
        kind: str | None = None,
        top_k: int = 4,
    ) -> list[dict]:
        """Search NetCo's procedures and reference documents (hybrid keyword + meaning search).
        Returns the best passages with a citation (e.g. "SOP-IH-001 v1.0 › S3") to quote.
        Optional filters: agent (inhome, access-pon, access-p2p, access-copper), carrier
        technology (GPON, XGSPON, VDSL, EOF, GOF) and kind (procedure or reference)."""
        filters = Filters(agent=agent, carrier_tech_type=carrier_tech_type, kind=kind)
        hits = await run(retriever.search, query, filters, max(1, min(top_k, 10)))
        return [hit.as_dict() for hit in hits]

    @server.tool(annotations=READ_ONLY)
    async def find_troubleshooting_sop(
        symptom: str, agent: str, carrier_tech_type: str | None = None
    ) -> dict:
        """Find the troubleshooting procedure (SOP) that fits the customer's problem for this
        agent and technology. Returns `found` and up to three candidates with their entry step.
        If `found` is false, no approved procedure applies: say so instead of improvising."""
        filters = Filters(agent=agent, carrier_tech_type=carrier_tech_type)
        candidates = await run(retriever.find_procedures, symptom, filters)
        return {"found": bool(candidates), "candidates": candidates}

    @server.tool(annotations=READ_ONLY)
    async def get_sop_procedure(doc_id: str) -> dict:
        """All steps of a procedure: actor, instruction, tool, whether approval is needed, the
        possible outcomes and how it ends."""
        doc = await run(document, doc_id)
        try:
            return procedure_view(doc)
        except NavigationError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(annotations=READ_ONLY)
    async def get_sop_step(doc_id: str, step_id: str) -> dict:
        """One step of a procedure, for example the entry step returned by
        find_troubleshooting_sop."""
        doc = await run(document, doc_id)
        try:
            return get_step(doc, step_id.strip().upper())
        except NavigationError as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(annotations=READ_ONLY)
    async def get_next_sop_step(doc_id: str, step_id: str, outcome: str) -> dict:
        """After carrying out a step, report the outcome you observed (one of the step's
        `outcomes`, e.g. "down") and get the next step to follow. The procedure, not you, decides
        the path."""
        doc = await run(document, doc_id)
        try:
            return next_step(doc, step_id.strip().upper(), outcome)
        except NavigationError as exc:
            raise ValueError(str(exc)) from exc

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        documents = await run(store.list_documents)
        return JSONResponse(
            {"status": "ok", "service": "knowledge-mcp", "documents": len(documents)}
        )

    @server.custom_route("/v1/documents", methods=["GET"])
    async def documents(_: Request) -> JSONResponse:
        return JSONResponse(await run(store.list_documents))

    upload_lock = threading.Lock()

    def add_document(filename: str, content: bytes, title: str | None, agents: list[str] | None):
        """Convert (Word, PDF ... through Docling; Markdown is used as it is), then ingest."""
        started = time.monotonic()
        if filename.lower().endswith(".md"):
            markdown, converted_by = content.decode("utf-8", errors="replace"), "as-is"
        elif converter is None:
            raise ConversionError("no document processor is configured")
        else:
            markdown, converted_by = converter.convert(filename, content), "docling"
        converted_s = time.monotonic() - started
        with upload_lock:  # one upload at a time, so ids and versions cannot clash
            doc_id, version = plan_upload(store.list_documents(), filename)
            doc = build_reference(
                markdown, doc_id=doc_id, version=version, filename=filename, title=title,
                agents=agents,
            )
            chunks = ingest_document(doc, store, retriever.embedder)
        logger.info("uploaded %s as %s v%s (%d chunks)", filename, doc_id, version, chunks)
        return {
            "doc_id": doc.doc_id, "version": doc.version, "title": doc.title, "kind": "reference",
            "source": doc.source, "sections": len(doc.sections), "chunks": chunks,
            "characters": len(markdown), "converted_by": converted_by,
            "convert_s": round(converted_s, 1), "total_s": round(time.monotonic() - started, 1),
        }

    @server.custom_route("/v1/documents/upload", methods=["POST"])
    async def upload(request: Request) -> JSONResponse:
        """Add a document (PDF, DOCX, PPTX, HTML or Markdown) as a searchable reference.

        Not an MCP tool: agents can read the knowledge base but never change it.
        """
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            return JSONResponse({"detail": "send the document as the form field 'file'"}, 400)
        filename = os.path.basename(file.filename or "").strip()
        extension = os.path.splitext(filename)[1].lower()
        if extension not in SUPPORTED:
            return JSONResponse(
                {"detail": f"unsupported file type '{extension}'; use {sorted(SUPPORTED)}"}, 415)
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"detail": "the file is larger than 15 MB"}, 413)
        agents = [a.strip() for a in str(form.get("agents") or "").split(",") if a.strip()]
        try:
            result = await run(
                add_document, filename, content, str(form.get("title") or "").strip() or None,
                agents or None)
        except ConversionError as exc:
            return JSONResponse({"detail": str(exc)}, 502)
        except DocumentInvalidError as exc:
            return JSONResponse({"detail": str(exc)}, 422)
        return JSONResponse(result, 201)

    @server.custom_route("/v1/documents/{doc_id}", methods=["DELETE"])
    async def delete_upload(request: Request) -> JSONResponse:
        doc_id = request.path_params["doc_id"].strip().upper()
        doc = await run(store.get_document, doc_id)
        if doc is None:
            return JSONResponse({"detail": f"unknown document '{doc_id}'"}, 404)
        if not doc.source.startswith(UPLOAD_PREFIX):
            return JSONResponse({"detail": "only uploaded documents can be removed here"}, 403)
        await run(store.delete, [doc_id])
        return JSONResponse({"deleted": doc_id})

    @server.custom_route("/v1/search", methods=["GET"])
    async def search(request: Request) -> JSONResponse:
        """Plain HTTP search for demos and end-to-end checks: /v1/search?q=...&agent=..."""
        params: dict[str, Any] = request.query_params
        query = params.get("q", "").strip()
        if not query:
            return JSONResponse({"detail": "query parameter 'q' is required"}, status_code=400)
        filters = Filters(
            agent=params.get("agent"), carrier_tech_type=params.get("tech"), kind=params.get("kind")
        )
        hits = await run(retriever.search, query, filters, int(params.get("top_k", 4)))
        return JSONResponse([hit.as_dict() for hit in hits])

    return server


def main() -> None:
    from knowledge_base import DoclingClient, OllamaEmbedder
    from knowledge_base.pg_store import PgVectorStore

    config = get_config()
    setup_logging("knowledge-mcp", config.log_level, config.log_json)
    store = PgVectorStore(
        config.database_url, schema=config.kb_schema, dimensions=config.embed_dimensions
    )
    store.ensure_schema()
    retriever = HybridRetriever(
        store, OllamaEmbedder(config), min_similarity=config.kb_min_similarity
    )
    port = int(os.getenv("MCP_PORT", "8105"))
    logger.info("starting knowledge MCP on port %s", port)
    create_server(
        retriever, host=os.getenv("MCP_HOST", "0.0.0.0"), port=port,
        converter=DoclingClient(config.docling_url),
    ).run(transport="streamable-http")
