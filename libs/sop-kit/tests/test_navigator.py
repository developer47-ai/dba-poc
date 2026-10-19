import pytest

from sop_kit import load_library, parse_sop
from sop_kit.navigator import NavigationError, get_step, next_step, procedure_view


@pytest.fixture
def doc(valid_text):
    return parse_sop(valid_text)


def test_next_step_follows_the_reported_outcome(doc):
    result = next_step(doc, "S1", "down")
    assert result["from_step"] == "S1" and result["outcome"] == "down"
    reboot = result["next_step"]
    assert reboot["step_id"] == "S2"
    assert reboot["tool"] == "network-diagnostics.reboot_device"
    assert reboot["requires_consent"] is True
    assert reboot["outcomes"] == ["done"]
    assert reboot["citation"] == "SOP-TST-001 v1.0 › S2"


def test_end_steps_report_how_the_procedure_ends(doc):
    end = next_step(doc, "S2", "done")["next_step"]
    assert end["end"] == "resolved" and end["outcomes"] == []


def test_invalid_outcome_lists_the_valid_ones(doc):
    with pytest.raises(NavigationError, match="choose one of: up, down"):
        next_step(doc, "S1", "maybe")


def test_no_next_step_after_an_end_step(doc):
    with pytest.raises(NavigationError, match="end step"):
        next_step(doc, "S3", "anything")


def test_unknown_step_is_reported(doc):
    with pytest.raises(NavigationError, match="has no step S9"):
        get_step(doc, "S9")


def test_procedure_view_lists_all_steps(doc):
    view = procedure_view(doc)
    assert view["entry_step"] == "S1"
    assert [s["step_id"] for s in view["steps"]] == ["S1", "S2", "S3"]


def test_stuck_modem_walkthrough_matches_the_scenario(library_dir):
    """C1002: PPP down, wan green + internet orange, reboot fixes it."""
    sop = {d.doc_id: d for d in load_library(library_dir)}["SOP-IH-001"]
    path, step = ["S1"], "S1"
    for outcome in ["down", "modem_stuck", "done", "up"]:
        step = next_step(sop, step, outcome)["next_step"]["step_id"]
        path.append(step)
    assert path == ["S1", "S2", "S3", "S4", "S5"]
    assert get_step(sop, "S3")["requires_consent"] is True
    assert get_step(sop, "S5")["end"] == "resolved"


def test_los_customer_walkthrough_hands_over_to_the_access_network(library_dir):
    """C1001 reaching the In-Home agent: the red LOS light means the fault is outside."""
    sop = {d.doc_id: d for d in load_library(library_dir)}["SOP-IH-001"]
    after_lights = next_step(sop, "S2", "access_problem")["next_step"]
    assert after_lights["end"] == "handover" and after_lights["handover_to"] == "access-network"
