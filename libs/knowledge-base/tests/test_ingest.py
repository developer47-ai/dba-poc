import pytest

from knowledge_base import HashingEmbedder, LibraryInvalidError, ingest_library
from sop_kit import chunk_document
from sop_kit.models import DocStatus


def test_first_ingest_adds_every_document(store, library, embedder):
    report = ingest_library(library, store, embedder)

    assert sorted(report.added) == sorted(d.doc_id for d in library)
    assert report.chunks == sum(len(chunk_document(d)) for d in library)
    listed = {d["doc_id"]: d for d in store.list_documents()}
    assert set(listed) == {d.doc_id for d in library}
    assert listed["SOP-IH-001"]["chunks"] == 12


def test_second_ingest_skips_unchanged_documents(store, library, embedder):
    ingest_library(library, store, embedder)
    report = ingest_library(library, store, embedder)
    assert report.added == [] and report.updated == [] and report.chunks == 0
    assert len(report.unchanged) == len(library)


def test_changed_document_is_replaced_and_retired_one_removed(store, library, embedder):
    ingest_library(library, store, embedder)
    changed = library[0].model_copy(update={"title": "Renamed", "checksum": "new"})
    retired = library[1].model_copy(update={"status": DocStatus.RETIRED})

    report = ingest_library([changed, retired, *library[2:]], store, embedder)

    assert report.updated == [changed.doc_id]
    assert report.removed == [retired.doc_id] and report.not_active == [retired.doc_id]
    assert store.get_document(changed.doc_id).title == "Renamed"
    assert store.get_document(retired.doc_id) is None


def test_invalid_library_is_rejected_before_anything_is_stored(store, library, embedder):
    broken = library[2].model_copy(update={"entry_step": "S99"})
    with pytest.raises(LibraryInvalidError, match="S99"):
        ingest_library([broken], store, embedder)
    store.ensure_schema()
    assert store.list_documents() == []


def test_wrong_embedding_size_is_rejected(store, library):
    with pytest.raises(ValueError, match="does not match"):
        ingest_library(library[:1], store, _WrongSize())


def test_full_procedure_is_stored_for_navigation(loaded, library):
    original = next(d for d in library if d.doc_id == "SOP-PON-001")
    stored = loaded.get_document("SOP-PON-001")
    assert stored.steps == original.steps and stored.entry_step == "S1"


class _WrongSize(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__(dimensions=1024)

    def embed_documents(self, texts):
        return [[0.1] * 8 for _ in texts]
