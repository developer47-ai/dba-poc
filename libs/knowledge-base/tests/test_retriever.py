from knowledge_base import Filters


def test_hybrid_search_ranks_the_right_procedure_step_first(retriever):
    hits = retriever.search("ONT LOS light red, no optical signal", Filters(agent="access-pon"))
    assert hits[0].chunk.doc_id == "SOP-PON-001"
    assert all(h.chunk.metadata["agent_scope"].count("access-pon") for h in hits)


def test_hits_carry_citations_and_scores(retriever):
    hits = retriever.search("reboot the modem remotely", Filters(agent="inhome"), top_k=3)
    citations = [h.citation for h in hits]
    assert "SOP-IH-001 v1.0 › S3" in citations
    top = hits[0].as_dict()
    assert top["citation"].startswith("SOP-IH-001 v1.0 › S")
    assert top["keyword_match"] is True and top["section"].startswith("Procedure")
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_reference_knowledge_is_found(retriever):
    hits = retriever.search("what does an orange power light mean", Filters(kind="reference"))
    assert hits[0].chunk.doc_id == "KB-LED-001"


def test_unrelated_question_returns_nothing(retriever):
    assert retriever.search("chocolate cake recipe") == []


def test_find_procedures_for_a_symptom(retriever):
    found = retriever.find_procedures(
        "internet light orange and PPP session down", Filters(agent="inhome")
    )
    assert found[0]["doc_id"] == "SOP-IH-001" and found[0]["entry_step"] == "S1"
    assert len({f["doc_id"] for f in found}) == len(found)


def test_find_procedures_respects_agent_and_technology(retriever):
    wifi = retriever.find_procedures("devices cannot connect to wifi", Filters(agent="inhome"))
    assert wifi[0]["doc_id"] == "SOP-IH-002"
    p2p = retriever.find_procedures(
        "WAN light red, access port down", Filters(agent="access-p2p", carrier_tech_type="EOF")
    )
    assert [f["doc_id"] for f in p2p][0] == "SOP-P2P-001"
    assert all(f["doc_id"] != "SOP-PON-001" for f in p2p)


def test_find_procedures_without_a_match(retriever):
    assert retriever.find_procedures("chocolate cake recipe", Filters(agent="inhome")) == []
