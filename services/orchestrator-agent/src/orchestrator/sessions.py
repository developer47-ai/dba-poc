"""Conversation sessions and their memory.

A session holds the short-term memory of a case: the chat history, where each agent is in its
procedure, the approval it waits for and the audit trail of decisions. `SessionStore` keeps it in
memory; `PgSessionStore` writes it to Postgres after every turn, so a case survives a restart and
an expert can resume it, or look at the earlier cases of the same customer.
"""

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from poc_common.events import ChatTurn

TURN_TIMEOUT_S = 900


class UnknownSessionError(KeyError):
    pass


@dataclass
class Session:
    customer_id: str
    expert_name: str | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    history: list[ChatTurn] = field(default_factory=list)
    active_agent: str | None = None
    # Resumable position per agent (e.g. the SOP step it waits at), from `agent_state` events.
    agent_state: dict[str, dict] = field(default_factory=dict)
    # The approval request waiting for the expert's decision (from a `consent_request` event),
    # and the audit trail of every decision taken in this session.
    pending_consent: dict | None = None
    consent_log: list[dict] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    busy_since: float | None = None

    @property
    def busy(self) -> bool:
        # A turn that never released the session (e.g. client vanished) expires after a while.
        return self.busy_since is not None and time.monotonic() - self.busy_since < TURN_TIMEOUT_S

    def start_turn(self) -> None:
        self.busy_since = time.monotonic()

    def end_turn(self) -> None:
        self.busy_since = None

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "customer_id": self.customer_id,
            "expert_name": self.expert_name,
            "created_at": self.created_at.isoformat(),
            "active_agent": self.active_agent,
            "agent_state": self.agent_state,
            "pending_consent": self.pending_consent,
            "consent_log": self.consent_log,
            "busy": self.busy,
            "history": [t.model_dump() for t in self.history],
        }

    def overview(self) -> dict:
        """One line per case for the "earlier cases" list."""
        asked = [t.content for t in self.history if t.role == "user"]
        return {
            "session_id": self.session_id,
            "customer_id": self.customer_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "active_agent": self.active_agent,
            "turns": len(asked),
            "first_question": asked[0][:120] if asked else None,
            "awaiting_approval": self.pending_consent is not None,
            "decisions": len(self.consent_log),
        }

    def to_record(self) -> dict:
        """What is stored: plain JSON-able data (no runtime flags such as `busy`)."""
        return {
            "session_id": self.session_id,
            "customer_id": self.customer_id,
            "expert_name": self.expert_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "active_agent": self.active_agent,
            "agent_state": self.agent_state,
            "pending_consent": self.pending_consent,
            "consent_log": self.consent_log,
            "history": [t.model_dump() for t in self.history],
        }

    @classmethod
    def from_record(cls, record: dict) -> "Session":
        return cls(
            session_id=record["session_id"],
            customer_id=record["customer_id"],
            expert_name=record["expert_name"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            active_agent=record["active_agent"],
            agent_state=record["agent_state"] or {},
            pending_consent=record["pending_consent"],
            consent_log=record["consent_log"] or [],
            history=[ChatTurn(**t) for t in record["history"] or []],
        )


class SessionStore:
    """Sessions in memory. Subclasses persist them: `save` is called after every change."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, customer_id: str, expert_name: str | None = None) -> Session:
        session = Session(customer_id=customer_id, expert_name=expert_name)
        self._sessions[session.session_id] = session
        self.save(session)
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise UnknownSessionError(session_id) from None

    def save(self, session: Session) -> None:
        session.updated_at = datetime.now(UTC)

    def list_for_customer(self, customer_id: str, limit: int = 10) -> list[Session]:
        """The customer's cases, most recently active first."""
        mine = [s for s in self._sessions.values() if s.customer_id == customer_id]
        return sorted(mine, key=lambda s: s.updated_at, reverse=True)[:limit]

    def close(self) -> None:
        pass


SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


class PgSessionStore(SessionStore):
    """Sessions in Postgres (one row per session, JSON for the nested memory).

    Sessions in use are also kept in memory, so a running turn always works on one object; after a
    restart a session is loaded from the database the first time it is asked for. The queries are
    tiny single-row statements on the local database, so they are made directly.
    """

    def __init__(self, dsn: str, *, schema: str = "agent") -> None:
        if not SCHEMA_NAME.match(schema):
            raise ValueError(f"invalid schema name: {schema!r}")
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        super().__init__()
        self.schema = schema
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=4, kwargs={"row_factory": dict_row}, open=True
        )
        self.ensure_schema()

    def ensure_schema(self) -> None:
        s = self.schema
        with self._pool.connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {s}.session (
                    session_id      text PRIMARY KEY,
                    customer_id     text NOT NULL,
                    expert_name     text,
                    created_at      timestamptz NOT NULL,
                    updated_at      timestamptz NOT NULL,
                    active_agent    text,
                    agent_state     jsonb NOT NULL,
                    pending_consent jsonb,
                    consent_log     jsonb NOT NULL,
                    history         jsonb NOT NULL)"""
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS session_customer "
                f"ON {s}.session (customer_id, updated_at DESC)"
            )

    def drop_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def get(self, session_id: str) -> Session:
        if session_id in self._sessions:
            return self._sessions[session_id]
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM {self.schema}.session WHERE session_id = %s", (session_id,)
            ).fetchone()
        if row is None:
            raise UnknownSessionError(session_id)
        # Another request may have loaded it meanwhile; keep a single object per session.
        return self._sessions.setdefault(session_id, Session.from_record(row))

    def save(self, session: Session) -> None:
        from psycopg.types.json import Jsonb

        super().save(session)
        record = session.to_record()
        pending = record["pending_consent"]
        with self._pool.connection() as conn:
            conn.execute(
                f"""INSERT INTO {self.schema}.session
                    (session_id, customer_id, expert_name, created_at, updated_at, active_agent,
                     agent_state, pending_consent, consent_log, history)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                      updated_at = EXCLUDED.updated_at, active_agent = EXCLUDED.active_agent,
                      agent_state = EXCLUDED.agent_state,
                      pending_consent = EXCLUDED.pending_consent,
                      consent_log = EXCLUDED.consent_log, history = EXCLUDED.history""",
                (
                    record["session_id"], record["customer_id"], record["expert_name"],
                    record["created_at"], record["updated_at"], record["active_agent"],
                    Jsonb(record["agent_state"]),
                    Jsonb(pending) if pending is not None else None,
                    Jsonb(record["consent_log"]), Jsonb(record["history"]),
                ),
            )

    def list_for_customer(self, customer_id: str, limit: int = 10) -> list[Session]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT session_id FROM {self.schema}.session WHERE customer_id = %s "
                "ORDER BY updated_at DESC LIMIT %s",
                (customer_id, limit),
            ).fetchall()
        return [self.get(row["session_id"]) for row in rows]

    def close(self) -> None:
        self._pool.close()