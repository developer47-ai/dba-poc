"""HTTP + SSE service around the In-Home agent."""

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import FastAPI, Header
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from inhome_agent.graph import build_graph
from inhome_agent.tools import load_read_only_tools
from poc_common.config import Config, get_config
from poc_common.events import Event, EventType
from poc_common.llm import get_chat_model, strip_thinking
from poc_common.logging import set_correlation_id, set_session_id, setup_logging

logger = logging.getLogger(__name__)
SOURCE = "inhome-agent"
MAX_GRAPH_STEPS = 14


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class DiagnoseRequest(BaseModel):
    customer_id: str
    message: str
    session_id: str | None = None
    history: list[Turn] = Field(default_factory=list)


def _to_message(turn: Turn):
    return HumanMessage(turn.content) if turn.role == "user" else AIMessage(turn.content)


async def diagnose(
    request: DiagnoseRequest, config: Config, llm: BaseChatModel
) -> AsyncIterator[Event]:
    def event(type_: EventType, **data) -> Event:
        return Event(type=type_, source=SOURCE, session_id=request.session_id, data=data)

    try:
        tools = await load_read_only_tools(config.diagnostics_mcp_url, request.customer_id)
        graph = build_graph(llm, tools)
        messages = [*map(_to_message, request.history), HumanMessage(request.message)]
        yield event(EventType.STATUS_UPDATE, step="diagnosis_started", tools=len(tools))

        calls: dict[str, dict] = {}
        async for update in graph.astream(
            {"messages": messages},
            {"recursion_limit": MAX_GRAPH_STEPS},
            stream_mode="updates",
        ):
            for node_update in update.values():
                for message in node_update["messages"]:
                    if isinstance(message, AIMessage) and message.tool_calls:
                        for call in message.tool_calls:
                            calls[call["id"]] = call
                            yield event(
                                EventType.TOOL_CALL,
                                tool=call["name"], arguments=call["args"], status="started",
                            )
                    elif isinstance(message, ToolMessage):
                        call = calls.get(message.tool_call_id, {})
                        yield event(
                            EventType.TOOL_CALL,
                            tool=call.get("name", message.name), status="completed",
                            result=_parse(message.content),
                        )
                    elif isinstance(message, AIMessage):
                        yield event(EventType.AGENT_MESSAGE, text=strip_thinking(message.text))
    except GraphRecursionError:
        yield event(EventType.ERROR, message="The agent did not reach an answer in time.")
    except Exception as exc:
        logger.exception("diagnosis failed")
        yield event(EventType.ERROR, message=f"Diagnosis failed: {exc}")
    yield event(EventType.DONE)


def _parse(content):
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return content


def create_app(config: Config | None = None, llm: BaseChatModel | None = None) -> FastAPI:
    config = config or get_config()
    app = FastAPI(title="In-Home Agent", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "agent": SOURCE}

    @app.post("/v1/diagnose")
    async def diagnose_endpoint(
        request: DiagnoseRequest,
        x_correlation_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        set_correlation_id(x_correlation_id)
        set_session_id(request.session_id)
        model = llm or get_chat_model(config)

        async def stream() -> AsyncIterator[str]:
            async for item in diagnose(request, config, model):
                item.correlation_id = x_correlation_id
                yield item.to_sse()

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def main() -> None:
    import uvicorn

    config = get_config()
    setup_logging(SOURCE, config.log_level, config.log_json)
    uvicorn.run(
        create_app(config), host=os.getenv("INHOME_HOST", "0.0.0.0"),
        port=int(os.getenv("INHOME_PORT", "8201")),
    )
