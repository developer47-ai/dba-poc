"""Orchestrator HTTP API: sessions and streamed conversation turns."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from orchestrator.routing import AgentRegistry, Router
from orchestrator.sessions import SessionStore, UnknownSessionError
from orchestrator.turns import SOURCE, run_turn
from poc_common.config import Config, get_config
from poc_common.events import ConsentResponse
from poc_common.logging import new_correlation_id, set_correlation_id, set_session_id, setup_logging
from poc_common.sse import sse_with_heartbeats
from poc_common.tracing import instrument_fastapi, setup_tracing

logger = logging.getLogger(__name__)


class CreateSessionRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    expert_name: str | None = Field(default=None, max_length=100)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def create_app(
    config: Config | None = None,
    *,
    registry: AgentRegistry | None = None,
    heartbeat_s: float = 15.0,
) -> FastAPI:
    config = config or get_config()
    registry = registry or AgentRegistry.from_config(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Agents on a CPU-only LLM can take minutes per turn, so the read timeout is generous.
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=600.0)) as client:
            app.state.http = client
            yield

    app = FastAPI(title="Orchestrator", version="0.1.0", lifespan=lifespan)
    app.state.sessions = SessionStore()
    app.state.router = Router(registry)
    instrument_fastapi(app)

    @app.exception_handler(UnknownSessionError)
    async def _unknown_session(_: Request, exc: UnknownSessionError):
        return JSONResponse(status_code=404, content={"detail": f"session {exc} not found"})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": SOURCE, "agents": registry.names()}

    @app.post("/v1/sessions", status_code=201)
    def create_session(body: CreateSessionRequest) -> dict:
        session = app.state.sessions.create(body.customer_id.strip(), body.expert_name)
        logger.info("session created for customer %s", session.customer_id)
        return session.summary()

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return app.state.sessions.get(session_id).summary()

    # Demo helpers for the chat UI: in a real deployment the CRM supplies the customer.
    async def simulator(method: str, path: str, **kwargs):
        url = f"{config.network_simulator_url.rstrip('/')}{path}"
        try:
            response = await app.state.http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"network simulator unavailable: {exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.json().get("detail", "error"))
        return response.json()

    async def carrier_tech(customer_id: str) -> str | None:
        """Fiber/copper technology of the customer (the installed base; here: the simulator)."""
        try:
            base = await simulator("GET", f"/customers/{customer_id}/installed-base")
        except HTTPException:
            return None
        return base.get("carrier_tech_type")

    app.state.router.carrier_tech = carrier_tech

    @app.get("/v1/demo/customers")
    async def demo_customers() -> list[dict]:
        return await simulator("GET", "/customers")

    @app.post("/v1/demo/customers/{customer_id}/reset")
    async def demo_reset_customer(customer_id: str) -> dict:
        return await simulator("POST", "/admin/reset", params={"customer_id": customer_id})

    def stream_turn(session, message: str, consent: ConsentResponse | None, correlation_id: str):
        async def events() -> AsyncIterator:
            try:
                async for item in run_turn(
                    session, message, router=app.state.router, client=app.state.http,
                    correlation_id=correlation_id, consent=consent,
                ):
                    yield item
            finally:
                session.end_turn()

        return StreamingResponse(
            sse_with_heartbeats(events(), heartbeat_s),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/sessions/{session_id}/messages")
    async def post_message(
        session_id: str,
        body: MessageRequest,
        x_correlation_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        session = app.state.sessions.get(session_id)
        if session.busy:
            raise HTTPException(409, "a turn is already running for this session")
        session.start_turn()
        correlation_id = set_correlation_id(x_correlation_id or new_correlation_id())
        set_session_id(session_id)
        return stream_turn(session, body.message, None, correlation_id)

    @app.post("/v1/sessions/{session_id}/consent")
    async def post_consent(
        session_id: str,
        body: ConsentResponse,
        x_correlation_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        """The expert's decision on the approval request the agent is waiting for.

        Only the pending request can be answered, and the action that runs is the one saved when
        the agent asked: the decision carries no arguments, so it cannot change what is executed.
        """
        session = app.state.sessions.get(session_id)
        if session.busy:
            raise HTTPException(409, "a turn is already running for this session")
        pending = session.pending_consent
        if pending is None or pending.get("consent_id") != body.consent_id:
            raise HTTPException(409, "no matching approval request is pending")
        session.start_turn()
        session.consent_log.append({
            "consent_id": body.consent_id, "action": pending["action"],
            "sop_id": pending.get("sop_id"), "step_id": pending.get("step_id"),
            "risk": pending.get("risk"), "approved": body.approved, "comment": body.comment,
            "decided_by": session.expert_name, "decided_at": datetime.now(UTC).isoformat(),
        })
        logger.info("consent %s for %s: %s", body.consent_id, pending["action"],
                    "approved" if body.approved else "declined")
        correlation_id = set_correlation_id(x_correlation_id or new_correlation_id())
        set_session_id(session_id)
        verdict = "Approved" if body.approved else "Declined"
        message = f"{verdict}: {pending['action']}" + (f" ({body.comment})" if body.comment else "")
        return stream_turn(session, message, body, correlation_id)

    return app


def main() -> None:
    import uvicorn

    config = get_config()
    setup_logging(SOURCE, config.log_level, config.log_json)
    setup_tracing(SOURCE, config)
    uvicorn.run(
        create_app(config),
        host=os.getenv("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.getenv("ORCHESTRATOR_PORT", "8000")),
    )
