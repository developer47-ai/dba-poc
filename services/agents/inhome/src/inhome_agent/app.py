"""HTTP + SSE service around the In-Home agent."""

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, Header
from fastapi.responses import StreamingResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from inhome_agent.graph import build_graph
from inhome_agent.tools import load_read_only_tools
from poc_common.config import Config, get_config
from poc_common.events import AgentRequest, ChatTurn, Event, EventType
from poc_common.llm import ThinkingFilter, get_chat_model, strip_thinking
from poc_common.logging import set_correlation_id, set_session_id, setup_logging

logger = logging.getLogger(__name__)
SOURCE = "inhome-agent"
MAX_GRAPH_STEPS = 14

DiagnoseRequest = AgentRequest


def _to_message(turn: ChatTurn):
    return HumanMessage(turn.content) if turn.role == "user" else AIMessage(turn.content)


async def diagnose(
    request: DiagnoseRequest, config: Config, llm: BaseChatModel
) -> AsyncIterator[Event]:
    """Run the agent and stream what happens, token by token.

    `agent_message_delta` events carry answer text as the model generates it. When the model
    message is complete an `agent_message` follows with the full text: `final=true` for the
    answer, `final=false` for text the model wrote before calling tools.
    """

    def event(type_: EventType, **data) -> Event:
        return Event(type=type_, source=SOURCE, session_id=request.session_id, data=data)

    try:
        tools = await load_read_only_tools(config.diagnostics_mcp_url, request.customer_id)
        graph = build_graph(llm, tools)
        messages = [*map(_to_message, request.history), HumanMessage(request.message)]
        yield event(EventType.STATUS_UPDATE, step="diagnosis_started", tools=len(tools))

        calls: dict[str, dict] = {}
        stream_filter: ThinkingFilter | None = None
        stream_id: str | None = None
        async for mode, payload in graph.astream(
            {"messages": messages},
            {"recursion_limit": MAX_GRAPH_STEPS},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "agent" or not isinstance(
                    chunk, AIMessageChunk
                ):
                    continue
                if stream_filter is None:
                    stream_filter, stream_id = ThinkingFilter(), chunk.id or uuid.uuid4().hex
                text = stream_filter.feed(chunk.text)
                if text:
                    yield event(EventType.AGENT_MESSAGE_DELTA, message_id=stream_id, text=text)
                continue

            for node_update in payload.values():
                for message in node_update["messages"]:
                    if isinstance(message, AIMessage):
                        message_id = stream_id or message.id or uuid.uuid4().hex
                        if stream_filter is not None:
                            rest = stream_filter.flush()
                            if rest:
                                yield event(EventType.AGENT_MESSAGE_DELTA,
                                            message_id=message_id, text=rest)
                        stream_filter, stream_id = None, None
                        text = strip_thinking(message.text)
                        if text or not message.tool_calls:
                            yield event(EventType.AGENT_MESSAGE, message_id=message_id,
                                        text=text, final=not message.tool_calls)
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
