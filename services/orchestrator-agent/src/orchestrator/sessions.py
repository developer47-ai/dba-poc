"""Conversation sessions (in memory for now; persisted in Postgres in the memory step)."""

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
            "busy": self.busy,
            "history": [t.model_dump() for t in self.history],
        }


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, customer_id: str, expert_name: str | None = None) -> Session:
        session = Session(customer_id=customer_id, expert_name=expert_name)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise UnknownSessionError(session_id) from None
