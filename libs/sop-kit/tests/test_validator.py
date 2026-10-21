import pytest

from sop_kit import load_library, parse_sop, validate, validate_library


def problems(text: str, **kwargs) -> list[str]:
    return validate(parse_sop(text), **kwargs)


def test_valid_document_has_no_problems(valid_text):
    assert problems(valid_text) == []


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("- next: { done: S3, declined: S3 }", "- next: { done: S9, declined: S3 }",
         "points to unknown step S9"),
        ("- requires_consent: true\n", "", "must set requires_consent: true"),
        ("network-diagnostics.get_ppp_session", "network-diagnostics.hack", "unknown tool"),
        ("entry_step: S1", "entry_step: S7", "entry_step 'S7'"),
        ("- end: resolved", "- end: escalate", "needs escalate_to"),
        ("- end: resolved", "- end: handover\n- handover_to: nobody", "needs handover_to"),
        ("- end: resolved", "- end: continue", "needs continue_with"),
        ("agent_scope: [inhome]", "agent_scope: [sales]", "agent_scope 'sales'"),
        ("doc_id: SOP-TST-001", "doc_id: proc-1", "doc_id 'proc-1'"),
        ("- actor: agent\n- tool: network-diagnostics.reboot_device",
         "- actor: customer\n- tool: network-diagnostics.reboot_device",
         "only actor 'agent' may call a tool"),
    ],
)
def test_broken_documents_are_reported(valid_text, old, new, expected):
    assert old in valid_text, "test setup: text to replace not found"
    found = problems(valid_text.replace(old, new, 1))
    assert any(expected in p for p in found), found


def test_unreachable_step_is_reported(valid_text):
    orphan = valid_text + "\n### S4 — Orphan\n- actor: expert\n- end: resolved\nNever used.\n"
    assert problems(orphan) == ["step S4 cannot be reached from S1"]


def test_loop_without_exit_is_reported(valid_text):
    looping = valid_text.replace(
        "- next: { done: S3, declined: S3 }", "- next: { done: S1, declined: S1 }"
    ).replace(
        "- next: { up: S3, down: S2 }", "- next: { up: S2, down: S2 }"
    )
    found = problems(looping)
    assert "step S1 can never reach an end (loop without exit)" in found
    assert "step S3 cannot be reached from S1" in found


def test_reference_to_missing_sop_is_reported(valid_text):
    text = valid_text.replace(
        "- end: resolved", "- end: continue\n- continue_with: SOP-XX-999"
    )
    found = problems(text, known_doc_ids={"SOP-TST-001"})
    assert any("SOP-XX-999, which is not in the library" in p for p in found)


def test_reference_documents_must_not_have_steps(valid_text):
    text = valid_text.replace("doc_id: SOP-TST-001", "doc_id: KB-TST-001").replace(
        "kind: procedure", "kind: reference"
    )
    assert "reference documents must not contain procedure steps" in problems(text)


def test_approval_step_without_a_decline_path_is_reported(valid_text):
    text = valid_text.replace("- next: { done: S3, declined: S3 }", "- next: { done: S3 }")
    found = problems(text)
    assert any("must have a 'declined' outcome" in p for p in found), found


def test_approval_step_needs_exactly_one_approved_outcome(valid_text):
    text = valid_text.replace(
        "- next: { done: S3, declined: S3 }", "- next: { done: S3, other: S3, declined: S3 }"
    )
    assert any("exactly one outcome besides 'declined'" in p for p in problems(text))


def test_the_whole_library_is_valid(library_dir):
    docs = load_library(library_dir)
    assert len(docs) >= 10
    assert validate_library(docs) == {}


def test_every_state_changing_step_in_the_library_asks_for_approval(library_dir):
    from sop_kit import TOOL_CATALOG

    for doc in load_library(library_dir):
        for step in doc.steps:
            if step.tool and TOOL_CATALOG[step.tool].changes_state:
                assert step.requires_consent, f"{doc.doc_id} {step.step_id}"
