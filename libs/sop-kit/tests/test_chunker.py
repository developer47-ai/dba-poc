from sop_kit import chunk_document, load_library, parse_sop
from sop_kit.chunker import MAX_CHARS


def test_summary_sections_and_one_chunk_per_step(valid_text):
    chunks = chunk_document(parse_sop(valid_text))

    types = [c.chunk_type for c in chunks]
    assert types == ["summary", "section", "section", "step", "step", "step"]
    assert [c.step_id for c in chunks if c.chunk_type == "step"] == ["S1", "S2", "S3"]
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_chunks_carry_context_and_metadata(valid_text):
    chunks = chunk_document(parse_sop(valid_text))
    reboot = next(c for c in chunks if c.step_id == "S2")

    assert reboot.text.startswith("SOP-TST-001 v1.0 › Test procedure › Procedure › S2 — Reboot\n")
    assert "Tool: network-diagnostics.reboot_device (requires approval)" in reboot.text
    assert "Outcomes: done → S3" in reboot.text
    assert reboot.metadata["agent_scope"] == ["inhome"]
    assert reboot.metadata["carrier_tech_types"] == ["GPON"]
    summary = chunks[0]
    assert "Symptoms: no internet" in summary.text and "Fix things." in summary.text


def test_long_sections_are_split_but_tables_stay_whole():
    table = "\n".join(["| a | b |", "|---|---|"] + [f"| row {i} | value {i} |" for i in range(80)])
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12))
    text = (
        "---\ndoc_id: KB-TST-001\ntitle: Long\nversion: 1\nkind: reference\n"
        "agent_scope: [inhome]\n---\n\n## Notes\n\n" + paragraphs + "\n\n" + table + "\n"
    )
    chunks = [c for c in chunk_document(parse_sop(text)) if c.chunk_type == "section"]

    assert len(chunks) > 1
    assert all(len(c.text) < 2 * MAX_CHARS for c in chunks if "| row" not in c.text)
    holding_table = [c for c in chunks if "| row 0 |" in c.text]
    assert len(holding_table) == 1 and "| row 79 |" in holding_table[0].text


def test_library_produces_step_level_citations(library_dir):
    chunks = [c for d in load_library(library_dir) for c in chunk_document(d)]
    reboot = [c for c in chunks if c.doc_id == "SOP-IH-001" and c.step_id == "S3"]
    assert len(reboot) == 1
    assert "network-diagnostics.reboot_device (requires approval)" in reboot[0].text
