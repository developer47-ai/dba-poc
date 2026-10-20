"""Event model streamed from the services to the chat UI over Server-Sent Events (SSE)."""

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SESSION_CREATED = "session_created"
    STATUS_UPDATE = "status_update"
    MEASUREMENT_STARTED = "measurement_started"
    MEASUREMENT_COMPLETED = "measurement_completed"
    AGENT_HANDOFF = "agent_handoff"
    TOOL_CALL = "tool_call"
    KNOWLEDGE_ARTICLE = "knowledge_article"
    AGENT_MESSAGE_DELTA = "agent_message_delta"
    AGENT_MESSAGE = "agent_message"
    CONSENT_REQUEST = "consent_request"
    CONSENT_RESPONSE = "consent_response"
    INCIDENT_SUGGESTION = "incident_suggestion"
    SWAP_ORDER_SUGGESTION = "swap_order_suggestion"
    APPOINTMENT_SUGGESTION = "appointment_suggestion"
    # Internal: an agent's resumable position (e.g. the SOP step it waits at). The orchestrator
    # stores it with the session and sends it back on the next turn; it is not shown to users.
    AGENT_STATE = "agent_state"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    DONE = "done"


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str | None = None
    type: EventType
    source: str = "system"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """Encode as one SSE frame: `event:` carries the type, `data:` the full JSON."""
        payload = self.model_dump_json()
        return f"id: {self.event_id}\nevent: {self.type.value}\ndata: {payload}\n\n"


def parse_sse(stream: str) -> list[Event]:
    """Decode SSE text (one or more frames) back into events; ignores comments."""
    events: list[Event] = []
    for frame in stream.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[5:].lstrip() for line in frame.split("\n") if line.startswith("data:")
        ]
        if data_lines:
            events.append(Event.model_validate(json.loads("\n".join(data_lines))))
    return events


SSE_HEARTBEAT_COMMENT = ": heartbeat\n\n"


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentRequest(BaseModel):
    """What the orchestrator sends to a specialist agent for one conversation turn."""

    customer_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    history: list[ChatTurn] = Field(default_factory=list)
    # The agent's own state from its last `agent_state` event in this session, if any.
    state: dict[str, Any] | None = None


class ConsentRequest(BaseModel):
    """Human-in-the-loop: an agent asks the support expert to approve a risky action."""

    consent_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    action: str
    description: str
    risk: str = "medium"
    arguments: dict[str, Any] = Field(default_factory=dict)
    sop_id: str | None = None
    step_id: str | None = None


class ConsentResponse(BaseModel):
    consent_id: str
    approved: bool
    comment: str | None = None


def consent_request_event(request: ConsentRequest, *, source: str, session_id: str | None) -> Event:
    return Event(
        type=EventType.CONSENT_REQUEST,
        source=source,
        session_id=session_id,
        data=request.model_dump(),
    )
