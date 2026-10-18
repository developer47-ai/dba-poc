"""Orchestrator HTTP API: sessions and streamed conversation turns."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from orchestrator.routing import AgentRegistry, Router
from orchestrator.sessions import SessionStore, UnknownSessionError
from orchestrator.turns import SOURCE, run_turn
from poc_common.config import Config, get_config
from poc_common.logging import new_correlation_id, set_correlation_id, set_session_id, setup_logging
from poc_common.sse import sse_with_heartbeats

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

        async def events() -> AsyncIterator:
            try:
                async for item in run_turn(
                    session, body.message, router=app.state.router,
                    client=app.state.http, correlation_id=correlation_id,
                ):
                    yield item
            finally:
                session.end_turn()

        return StreamingResponse(
            sse_with_heartbeats(events(), heartbeat_s),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def main() -> None:
    import uvicorn

    config = get_config()
    setup_logging(SOURCE, config.log_level, config.log_json)
    uvicorn.run(
        create_app(config),
        host=os.getenv("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.getenv("ORCHESTRATOR_PORT", "8000")),
    )
