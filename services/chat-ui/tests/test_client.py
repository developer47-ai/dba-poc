import httpx
import pytest

from chat_ui.client import KnowledgeClient, OrchestratorClient, OrchestratorError
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


def test_stream_consent_sends_the_decision_and_streams_the_follow_up(orchestrator_url):
    client = OrchestratorClient(orchestrator_url)
    session_id = client.create_session("C1002", "Sam")["session_id"]
    list(client.stream_turn(session_id, "no internet"))  # the agent asks for approval (c1)

    events = list(client.stream_consent(session_id, "c1", True, "customer agreed"))

    assert events[-1].type is EventType.DONE
    with pytest.raises(OrchestratorError, match="HTTP 409"):
        list(client.stream_consent(session_id, "other", True))

def test_earlier_cases_can_be_listed_and_reopened(orchestrator_url, answer):
    client = OrchestratorClient(orchestrator_url)
    session_id = client.create_session("C1005", "Sam")["session_id"]
    list(client.stream_turn(session_id, "port is down"))

    cases = client.list_sessions("C1005")
    reopened = client.get_session(session_id)

    assert cases[0]["session_id"] == session_id and cases[0]["first_question"] == "port is down"
    assert [t["role"] for t in reopened["history"]] == ["user", "assistant"]
    assert reopened["history"][1]["content"] == answer
    assert reopened["pending_consent"]["action"] == "reboot_device"
    assert client.list_sessions("C0000") == []
    with pytest.raises(OrchestratorError, match="HTTP 404"):
        client.get_session("missing")

def knowledge_client(handler) -> KnowledgeClient:
    return KnowledgeClient("http://kb", http=httpx.Client(
        base_url="http://kb", transport=httpx.MockTransport(handler)))


def test_a_document_is_uploaded_with_its_title_and_listed_among_the_uploads():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = request.read()
            return httpx.Response(201, json={"doc_id": "KB-UPL-001", "version": "1.0"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted": request.url.path.rsplit("/", 1)[1]})
        return httpx.Response(200, json=[
            {"doc_id": "SOP-IH-001", "source": "knowledge/sop/a.md"},
            {"doc_id": "KB-UPL-001", "source": "upload:guide.docx"},
        ])

    client = knowledge_client(handler)

    assert client.upload("guide.docx", b"PK", title="ONT guide")["doc_id"] == "KB-UPL-001"
    assert b'filename="guide.docx"' in seen["body"] and b"ONT guide" in seen["body"]
    assert [d["doc_id"] for d in client.uploaded_documents()] == ["KB-UPL-001"]
    assert client.remove("KB-UPL-001") == {"deleted": "KB-UPL-001"}


def test_upload_problems_are_readable():
    refusal = {"detail": "the document processor is not reachable"}
    client = knowledge_client(lambda request: httpx.Response(502, json=refusal))
    with pytest.raises(OrchestratorError, match="HTTP 502: the document processor"):
        client.upload("a.pdf", b"x")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OrchestratorError, match="cannot reach the knowledge service"):
        knowledge_client(down).uploaded_documents()