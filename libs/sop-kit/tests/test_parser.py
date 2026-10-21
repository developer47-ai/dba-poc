import pytest

from sop_kit import Actor, DocKind, SopParseError, StepEnd, parse_sop


def test_front_matter_sections_and_steps_are_parsed(valid_text):
    doc = parse_sop(valid_text, "test.md")

    assert doc.doc_id == "SOP-TST-001" and doc.kind is DocKind.PROCEDURE
    assert doc.version == "1.0"  # YAML number kept as text
    assert doc.entry_step == "S1"
    assert [s.title for s in doc.sections] == ["Purpose", "Procedure"]
    assert doc.sections[1].text == "Intro text before the first step."
    assert [s.step_id for s in doc.steps] == ["S1", "S2", "S3"]
    assert len(doc.checksum) == 64 and doc.source == "test.md"


def test_step_fields_and_instruction(valid_text):
    first, reboot, done = parse_sop(valid_text).steps

    assert first.actor is Actor.AGENT
    assert first.tool == "network-diagnostics.get_ppp_session"
    assert first.next == {"up": "S3", "down": "S2"}
    assert first.instruction == "Read the PPP status.\nOutcome `up` when the status is up."
    assert reboot.requires_consent is True
    assert done.end is StepEnd.RESOLVED and done.is_terminal and not done.next


def test_missing_front_matter_is_an_error():
    with pytest.raises(SopParseError, match="front matter"):
        parse_sop("## Purpose\nNo header", "bad.md")


def test_invalid_next_field_is_an_error(valid_text):
    broken = valid_text.replace("- next: { done: S3, declined: S3 }", "- next: S3")
    with pytest.raises(SopParseError, match="mapping"):
        parse_sop(broken, "bad.md")


def test_unknown_actor_is_an_error(valid_text):
    with pytest.raises(SopParseError, match="S3"):
        parse_sop(valid_text.replace("- actor: expert", "- actor: robot"), "bad.md")
