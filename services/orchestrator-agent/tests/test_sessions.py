"""Session memory: what is stored, what survives a restart, and what the API offers on top.

The store contract runs against the in-memory store and Postgres (the Postgres run needs
RUN_PG_TESTS=1 and DATABASE_URL, as in CI). The app tests use a store that really serializes the
sessions, so a "restart" (a new app on the same storage) works without a database.
"""

import os
import time
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from orchestrator import create_app
from orchestrator.routing import AgentRegistry
from orchestrator.sessions import PgSessionStore, Session, SessionStore, UnknownSessionError
from orchestrator.turns import MAX_AGENT_HISTORY
from poc_common.config import Config
from poc_common.events import ChatTurn, EventType, parse_sse

RUN_PG = os.getenv("RUN_PG_TESTS") == "1"

ASK = {"consent_id": "c-1", "action": "reboot_device", "sop_id": "SOP-IH-001", "step_id": "S3",
       "risk": "medium", "description": "Reboot the modem", "arguments": {}}


def fill(session: Session) -> Session:
    session.history = [ChatTurn(role="user", content="no internet"),
                       ChatTurn(role="assistant", content="PPP is down.")]
    session.active_agent = "inhome-agent"
    session.agent_state = {"inhome-agent": {"sop": {"doc_id": "SOP-IH-001", "step_id": "S3",
                                                    "awaiting": "consent"}}}
    session.pending_consent = {**ASK, "agent": "inhome-agent"}
    session.consent_log = [{"consent_id": "c-0", "approved": True, "decided_by": "Sam"}]
    return session


class SerializingStore(SessionStore):
    """Keeps only what a database would keep: after `save`, `get` rebuilds from the record."""

    def __init__(self, records: dict | None = None) -> None:
        super().__init__()
        self.records = records if records is not None else {}
        self.saves = 0

    def save(self, session: Session) -> None:
        super().save(session)
        self.saves += 1
        self.records[session.session_id] = session.to_record()

    def get(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            if session_id not in self.records:
                raise UnknownSessionError(session_id)
            self._sessions[session_id] = Session.from_record(self.records[session_id])
        return self._sessions[session_id]

    def list_for_customer(self, customer_id: str, limit: int = 10) -> list[Session]:
        rows = [r for r in self.records.values() if r["customer_id"] == customer_id]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return [self.get(r["session_id"]) for r in rows[:limit]]


@pytest.fixture(params=["memory", pytest.param("pg", marks=pytest.mark.pg)])
def store_factory(request):
    """() -> a store on the same storage; call it twice to simulate a restart (pg only)."""
    if request.param == "memory":
        store = SessionStore()
        yield lambda: store, False
        return
    if not RUN_PG:
        pytest.skip("needs Postgres; set RUN_PG_TESTS=1 and DATABASE_URL")
    schema = f"agent_test_{uuid.uuid4().hex[:8]}"
    opened: list[PgSessionStore] = []

    def open_store() -> PgSessionStore:
        opened.append(PgSessionStore(Config().database_url, schema=schema))
        return opened[-1]

    yield open_store, True
    opened[0].drop_schema()
    for each in opened:
        each.close()


def test_a_stored_session_comes_back_with_all_its_memory(store_factory):
    factory, restartable = store_factory
    store = factory()
    created = fill(store.create("C1002", "Sam"))
    store.save(created)

    loaded = factory().get(created.session_id) if restartable else store.get(created.session_id)

    assert loaded.customer_id == "C1002" and loaded.expert_name == "Sam"
    assert [(t.role, t.content) for t in loaded.history] == [("user", "no internet"),
                                                              ("assistant", "PPP is down.")]
    assert loaded.active_agent == "inhome-agent"
    assert loaded.agent_state["inhome-agent"]["sop"]["step_id"] == "S3"
    assert loaded.pending_consent["consent_id"] == "c-1"
    assert loaded.consent_log == [{"consent_id": "c-0", "approved": True, "decided_by": "Sam"}]
    assert loaded.created_at == created.created_at and not loaded.busy


def test_no_pending_approval_stays_empty_and_changes_are_updated(store_factory):
    factory, restartable = store_factory
    store = factory()
    session = store.create("C1003")
    fresh = factory().get(session.session_id) if restartable else session
    assert fresh.pending_consent is None

    session.pending_consent = {**ASK, "agent": "inhome-agent"}
    store.save(session)
    session.pending_consent = None
    session.history.append(ChatTurn(role="user", content="thanks"))
    store.save(session)

    again = factory().get(session.session_id) if restartable else store.get(session.session_id)
    assert again.pending_consent is None and again.history[-1].content == "thanks"


def test_unknown_session_is_reported(store_factory):
    factory, _ = store_factory
    with pytest.raises(UnknownSessionError):
        factory().get("nope")


def test_earlier_cases_are_listed_per_customer_newest_first_and_limited(store_factory):
    factory, _ = store_factory
    store = factory()
    first = store.create("C1001")
    second = store.create("C1001")
    other = store.create("C1002")
    time.sleep(0.01)
    store.save(first)  # touched last, so it comes first

    listed = [s.session_id for s in store.list_for_customer("C1001")]

    assert listed == [first.session_id, second.session_id]
    assert other.session_id not in listed
    assert len(store.list_for_customer("C1001", limit=1)) == 1
    assert store.list_for_customer("C9999") == []


def test_invalid_schema_name_is_refused():
    with pytest.raises(ValueError):
        PgSessionStore("postgresql://unused", schema="x; DROP TABLE y")


def test_overview_is_a_one_line_summary():
    session = fill(Session(customer_id="C1002"))
    overview = session.overview()
    assert overview["turns"] == 1 and overview["first_question"] == "no internet"
    assert overview["awaiting_approval"] is True and overview["decisions"] == 1
    assert Session(customer_id="C1").overview()["first_question"] is None


def test_record_has_no_runtime_flags_and_timestamps_are_aware():
    session = Session(customer_id="C1")
    session.start_turn()
    record = session.to_record()
    assert "busy" not in record and "busy_since" not in record
    assert record["created_at"].tzinfo is not None and isinstance(record["updated_at"], datetime)


# ---- through the API -------------------------------------------------------------------------


def test_a_case_survives_a_restart_and_the_pending_approval_can_still_be_decided(fake_agent):
    agent_url, calls = fake_agent(ask_consent=ASK)
    records: dict = {}
    registry = AgentRegistry({"inhome-agent": agent_url})

    with TestClient(create_app(Config(), registry=registry, store=SerializingStore(records))) as c1:
        session_id = c1.post("/v1/sessions", json={"customer_id": "C1002",
                                                    "expert_name": "Sam"}).json()["session_id"]
        c1.post(f"/v1/sessions/{session_id}/messages", json={"message": "no internet"})
        assert records[session_id]["pending_consent"]["consent_id"] == "c-1"

    # A new orchestrator process on the same storage.
    with TestClient(create_app(Config(), registry=registry, store=SerializingStore(records))) as c2:
        summary = c2.get(f"/v1/sessions/{session_id}").json()
        assert summary["history"][0]["content"] == "no internet"
        assert summary["pending_consent"]["consent_id"] == "c-1"
        assert summary["active_agent"] == "inhome-agent" and summary["busy"] is False

        decided = c2.post(f"/v1/sessions/{session_id}/consent",
                          json={"consent_id": "c-1", "approved": True})
        events = parse_sse(decided.text)
        assert decided.status_code == 200 and events[-1].type is EventType.DONE
        assert calls[-1]["json"]["consent"]["approved"] is True
        assert records[session_id]["consent_log"][0]["decided_by"] == "Sam"
        assert records[session_id]["pending_consent"] is None


def test_unknown_session_is_still_404_with_persistence(fake_agent):
    agent_url, _ = fake_agent()
    app = create_app(Config(), registry=AgentRegistry({"inhome-agent": agent_url}),
                     store=SerializingStore())
    with TestClient(app) as client:
        assert client.get("/v1/sessions/missing").status_code == 404


def test_earlier_cases_endpoint_lists_the_customers_sessions(fake_agent):
    agent_url, _ = fake_agent()
    store = SerializingStore()
    app = create_app(Config(), registry=AgentRegistry({"inhome-agent": agent_url}), store=store)
    with TestClient(app) as client:
        ids = [client.post("/v1/sessions", json={"customer_id": cid}).json()["session_id"]
               for cid in ("C1001", "C1001", "C1002")]
        client.post(f"/v1/sessions/{ids[0]}/messages", json={"message": "no internet"})

        listed = client.get("/v1/sessions", params={"customer_id": "C1001"}).json()

        assert [c["session_id"] for c in listed] == [ids[0], ids[1]]
        assert listed[0]["turns"] == 1 and listed[0]["first_question"] == "no internet"
        assert client.get("/v1/sessions", params={"customer_id": "C9"}).json() == []
        assert client.get("/v1/sessions").status_code == 422


def test_agents_get_a_window_of_the_history_but_the_chat_keeps_everything(fake_agent):
    agent_url, calls = fake_agent()
    store = SerializingStore()
    app = create_app(Config(), registry=AgentRegistry({"inhome-agent": agent_url}), store=store)
    with TestClient(app) as client:
        session_id = client.post("/v1/sessions", json={"customer_id": "C1002"}).json()["session_id"]
        turns = MAX_AGENT_HISTORY  # twice as many messages as the window
        for n in range(turns):
            client.post(f"/v1/sessions/{session_id}/messages", json={"message": f"question {n}"})

        sent = calls[-1]["json"]["history"]
        assert len(sent) == MAX_AGENT_HISTORY
        assert sent[-2]["content"] == f"question {turns - 2}"
        stored = client.get(f"/v1/sessions/{session_id}").json()["history"]
        assert len(stored) == 2 * turns and stored[0]["content"] == "question 0"
