import pytest

from chat_ui.client import OrchestratorClient, OrchestratorError
from poc_common.events import EventType


def test_lists_customers_and_creates_a_session(orchestrator_url):
    client = OrchestratorClient(orchestrator_url)
    ids = [c["customer_id"] for c in client.list_customers()]
    assert ids == ["C1001", "C1002", "C1003", "C1004", "C1005", "C1006"]
    session = client.create_session("C1002", "Sam")
    assert session["customer_id"] == "C1002" and session["expert_name"] == "Sam"


def test_stream_turn_yields_events_in_order(orchestrator_url, answer):
    client = OrchestratorClient(orchestrator_url)
    session_id = client.create_session("C1002")["session_id"]

    events = list(client.stream_turn(session_id, "no internet"))

    types = [e.type for e in events]
    assert types[:2] == [EventType.STATUS_UPDATE, EventType.AGENT_HANDOFF]
    assert types[-1] is EventType.DONE
    deltas = [e.data["text"] for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    assert "".join(deltas) == answer


def test_reset_customer(orchestrator_url):
    assert OrchestratorClient(orchestrator_url).reset_customer("C1003")["status"] == "reset"


def test_errors_are_readable(orchestrator_url):
    client = OrchestratorClient(orchestrator_url)
    with pytest.raises(OrchestratorError, match="HTTP 404"):
        list(client.stream_turn("missing-session", "hi"))
    with pytest.raises(OrchestratorError, match="HTTP 404"):
        client.reset_customer("C9999")
    with pytest.raises(OrchestratorError, match="cannot reach"):
        OrchestratorClient("http://127.0.0.1:9").list_customers()
