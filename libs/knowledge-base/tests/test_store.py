from knowledge_base import Filters


def docs_of(store, results) -> set[str]:
    chunks = store.get_chunks([chunk_id for chunk_id, _ in results])
    return {c.doc_id for c in chunks.values()}


def test_keyword_search_finds_exact_terms(loaded):
    results = loaded.keyword_search("LOS", Filters(), 20)
    assert results
    for chunk in loaded.get_chunks([c for c, _ in results]).values():
        assert "los" in chunk.text.lower()
    # SOP-IH-001 S2 mentions the ONT `los` light when deciding it is an access problem.
    assert docs_of(loaded, results) <= {"SOP-PON-001", "SOP-PON-002", "KB-LED-001", "SOP-IH-001"}


def test_keyword_search_matches_plural_and_singular(loaded):
    singular = docs_of(loaded, loaded.keyword_search("light", Filters(), 50))
    plural = docs_of(loaded, loaded.keyword_search("lights", Filters(), 50))
    assert "KB-LED-001" in singular and "KB-LED-001" in plural


def test_query_without_known_terms_finds_nothing(loaded):
    assert loaded.keyword_search("the of and", Filters(), 10) == []
    assert loaded.keyword_search("zzqx", Filters(), 10) == []


def test_agent_filter_keeps_each_agent_to_its_documents(loaded):
    results = loaded.vector_search([0.03] * 1024, Filters(agent="access-p2p"), 100)
    assert docs_of(loaded, results) == {"SOP-P2P-001", "SOP-ESC-001", "KB-LED-001"}


def test_technology_filter(loaded):
    results = loaded.vector_search([0.03] * 1024, Filters(carrier_tech_type="vdsl"), 100)
    docs = docs_of(loaded, results)
    assert "SOP-CU-001" in docs and "SOP-PON-001" not in docs and "SOP-P2P-001" not in docs


def test_kind_and_chunk_type_filters(loaded):
    reference = docs_of(loaded, loaded.vector_search([0.03] * 1024, Filters(kind="reference"), 100))
    assert reference == {"KB-LED-001", "KB-WIFI-001"}
    steps = loaded.get_chunks(
        [c for c, _ in loaded.vector_search([0.03] * 1024, Filters(chunk_types=("step",)), 200)]
    )
    assert steps and all(c.chunk_type == "step" and c.step_id for c in steps.values())


def test_vector_search_returns_similarity_best_first(loaded, embedder):
    query = embedder.embed_query("Reboot the modem remotely")
    results = loaded.vector_search(query, Filters(), 5)
    similarities = [s for _, s in results]
    assert similarities == sorted(similarities, reverse=True)
    best = loaded.get_chunks([results[0][0]])[results[0][0]]
    assert best.doc_id == "SOP-IH-001" and best.step_id == "S3"
