import httpx
import pytest

from knowledge_base import (
    ConversionError,
    DoclingClient,
    DocumentInvalidError,
    HybridRetriever,
    build_reference,
    ingest_document,
    ingest_library,
    plan_upload,
    split_sections,
)

GUIDE = """<!-- image -->
# FibreLink ONT-100 quick guide

The ONT-100 is the fiber termination unit installed at the customer.

## Factory reset

Hold the recessed reset button for **12 seconds** with a paperclip.

### After the reset

The ONT restarts within three minutes and must be provisioned again.

## Optical levels

| Level | Meaning |
|---|---|
| -8 to -27 dBm | Normal receive power |
| below -27 dBm | Signal too weak |

```
# not a heading
```
"""


def test_headings_of_any_level_become_flat_sections_with_their_path():
    title, sections = split_sections(GUIDE)

    assert title == "FibreLink ONT-100 quick guide"
    assert [s.title for s in sections] == [
        "Overview", "Factory reset", "Factory reset › After the reset", "Optical levels"]
    assert sections[0].text.startswith("The ONT-100 is the fiber termination unit")
    assert "| below -27 dBm | Signal too weak |" in sections[3].text
    assert "# not a heading" in sections[3].text, "code blocks are not headings"
    assert "image" not in sections[0].text, "HTML comments such as image placeholders are dropped"


def test_a_document_without_headings_is_one_overview_and_empty_sections_are_skipped():
    assert split_sections("Just some text.")[1][0].title == "Overview"
    title, sections = split_sections("# Only a title\n\n## Empty\n\n## Filled\n\nText")
    assert title == "Only a title" and [s.title for s in sections] == ["Filled"]
    assert split_sections("") == (None, [])


def test_new_uploads_get_the_next_free_id_and_the_same_file_gets_a_new_version():
    existing = [
        {"doc_id": "SOP-IH-001", "source": "knowledge/sop/x.md", "version": "1.0"},
        {"doc_id": "KB-UPL-001", "source": "upload:a.docx", "version": "1.0"},
        {"doc_id": "KB-UPL-003", "source": "upload:b.pdf", "version": "2.4"},
    ]
    assert plan_upload(existing, "new.pdf") == ("KB-UPL-002", "1.0")
    assert plan_upload(existing, "a.docx") == ("KB-UPL-001", "1.1")
    assert plan_upload(existing, "b.pdf") == ("KB-UPL-003", "2.5")
    assert plan_upload([], "x.pdf") == ("KB-UPL-001", "1.0")


def test_an_upload_is_a_reference_document_that_can_never_be_a_procedure():
    doc = build_reference(GUIDE, doc_id="KB-UPL-001", version="1.0", filename="ont_100-guide.docx")

    assert doc.kind.value == "reference" and doc.steps == []
    assert doc.source == "upload:ont_100-guide.docx"
    assert doc.title == "FibreLink ONT-100 quick guide"
    assert {"inhome", "access-pon"} <= set(doc.agent_scope)
    named = build_reference(GUIDE, doc_id="KB-UPL-001", version="1.0", filename="a.docx",
                            title="Custom", agents=["access-pon"])
    assert named.title == "Custom" and named.agent_scope == ["access-pon"]
    untitled = build_reference("text only", doc_id="KB-UPL-002", version="1.0",
                               filename="modem_quick-start.pdf")
    assert untitled.title == "modem quick start"


def test_unusable_uploads_are_refused():
    with pytest.raises(DocumentInvalidError, match="no sections"):
        build_reference("   ", doc_id="KB-UPL-001", version="1.0", filename="empty.docx")
    with pytest.raises(DocumentInvalidError, match="agent_scope"):
        build_reference(GUIDE, doc_id="KB-UPL-001", version="1.0", filename="a.docx",
                        agents=["nobody"])


def test_an_ingested_upload_is_found_and_cited(store, embedder):
    doc = build_reference(GUIDE, doc_id="KB-UPL-001", version="1.0", filename="guide.docx")
    chunks = ingest_document(doc, store, embedder)

    hits = HybridRetriever(store, embedder, min_similarity=0.2).search(
        "how long to hold the reset button", top_k=3)

    assert chunks == 5  # summary + four sections
    assert hits and hits[0].chunk.doc_id == "KB-UPL-001"
    assert "12 seconds" in hits[0].chunk.text
    assert [d["source"] for d in store.list_documents()] == ["upload:guide.docx"]


def test_the_library_sync_keeps_uploads_but_still_removes_library_documents(
    store, library, embedder
):
    ingest_library(library, store, embedder)
    ingest_document(
        build_reference(GUIDE, doc_id="KB-UPL-001", version="1.0", filename="guide.docx"),
        store, embedder)

    report = ingest_library(library, store, embedder)
    assert report.removed == [] and "KB-UPL-001" in store.checksums()

    vanished = next(d for d in library if d.doc_id.startswith("KB-"))
    report = ingest_library([d for d in library if d is not vanished], store, embedder)
    assert report.removed == [vanished.doc_id] and "KB-UPL-001" in store.checksums()


def test_a_new_version_replaces_the_old_one(store, embedder):
    first = build_reference(GUIDE, doc_id="KB-UPL-001", version="1.0", filename="g.docx")
    ingest_document(first, store, embedder)
    second = build_reference(GUIDE.replace("12 seconds", "15 seconds"), doc_id="KB-UPL-001",
                             version="1.1", filename="g.docx")
    ingest_document(second, store, embedder)

    assert [(d["doc_id"], d["version"]) for d in store.list_documents()] == [("KB-UPL-001", "1.1")]
    text = " ".join(c.text for c in store.get_chunks(
        [f"KB-UPL-001:1.1:{n}" for n in range(5)]).values())
    assert "15 seconds" in text and "12 seconds" not in text


# ---- the Docling client ----------------------------------------------------------------------


def client_for(handler) -> DoclingClient:
    return DoclingClient("http://docling:5001/", client=httpx.Client(
        transport=httpx.MockTransport(handler)))


def test_docling_client_sends_the_file_and_returns_markdown():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.read()
        return httpx.Response(200, json={
            "status": "success", "errors": [], "document": {"md_content": "# Title\n\nText\n"}})

    markdown = client_for(handler).convert("guide.docx", b"PK-binary")

    assert markdown == "# Title\n\nText"
    assert seen["url"] == "http://docling:5001/v1/convert/file"
    assert b'name="files"; filename="guide.docx"' in seen["body"] and b"PK-binary" in seen["body"]
    assert b'name="do_ocr"' in seen["body"] and b"false" in seen["body"]


@pytest.mark.parametrize("response, message", [
    (httpx.Response(500, json={"detail": "boom"}), "refused the file: boom"),
    (httpx.Response(200, json={"status": "failure", "errors": ["bad pdf"], "document": {}}),
     "bad pdf"),
    (httpx.Response(200, json={"status": "success", "document": {"md_content": " "}}),
     "no text found"),
])
def test_docling_client_reports_failures(response, message):
    with pytest.raises(ConversionError, match=message):
        client_for(lambda request: response).convert("x.pdf", b"x")


def test_docling_client_reports_an_unreachable_service():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ConversionError, match="not reachable"):
        client_for(down).convert("x.pdf", b"x")

# What Docling really produced for data/documents (docling 2.142): a Word file keeps its title as
# `#`, a PDF has only `##` headings and tables are pipe tables.
DOCLING_PDF = """## FibreLink ONT-200 installation sheet

The ONT-200 connects customers to an XGS-PON line. Install it near the fiber wall outlet.

## Power supply

Use only the supplied 12 V DC power adapter.

## PON light

| PON light      | Meaning                                   |
|----------------|-------------------------------------------|
| Blinking green | Ranging: the ONT is searching for the OLT |
| Solid green    | Registered at the OLT                     |
"""


def test_real_docling_output_for_a_pdf_without_a_title_heading():
    title, sections = split_sections(DOCLING_PDF)

    assert title is None
    assert [s.title for s in sections] == [
        "FibreLink ONT-200 installation sheet", "Power supply", "PON light"]
    assert "Blinking green | Ranging" in sections[2].text
    doc = build_reference(DOCLING_PDF, doc_id="KB-UPL-002", version="1.0",
                          filename="FibreLink-ONT-200-installation-sheet.pdf")
    assert doc.title == "FibreLink ONT 200 installation sheet"