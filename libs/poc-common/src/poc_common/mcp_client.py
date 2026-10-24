"""Thin async client for MCP servers over streamable HTTP.

A short-lived session per call keeps the POC simple and stateless; the correlation id is
forwarded as an HTTP header so traces can be joined across services.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from poc_common.logging import CORRELATION_HEADER, get_correlation_id
from poc_common.tracing import annotate, span


class McpToolError(RuntimeError):
    """The MCP server reported an error while executing a tool."""

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"MCP tool '{tool}' failed: {message}")
        self.tool = tool


@asynccontextmanager
async def mcp_session(
    url: str,
    *,
    correlation_id: str | None = None,
    timeout_s: float = 30,
    read_timeout_s: float = 300,
) -> AsyncIterator[ClientSession]:
    headers: dict[str, str] = {}
    correlation_id = correlation_id or get_correlation_id()
    if correlation_id:
        headers[CORRELATION_HEADER] = correlation_id
    async with create_mcp_http_client(
        headers=headers,
        timeout=httpx.Timeout(timeout_s, read=read_timeout_s),
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def parse_tool_result(tool: str, result: Any) -> Any:
    """Turn an MCP CallToolResult into plain Python data (dict/list/str/number)."""
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    if result.isError:
        raise McpToolError(tool, " ".join(texts) or "unknown error")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured

    text = "\n".join(texts)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


async def call_tool(
    url: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    correlation_id: str | None = None,
    timeout_s: float = 30,
    read_timeout_s: float = 300,
) -> Any:
    with span("mcp.call_tool", {"mcp.tool": name, "mcp.url": url}):
        async with mcp_session(
            url, correlation_id=correlation_id, timeout_s=timeout_s, read_timeout_s=read_timeout_s
        ) as session:
            result = await session.call_tool(name, arguments or {})
        try:
            return parse_tool_result(name, result)
        except McpToolError as exc:
            annotate({"mcp.error": str(exc)[:200]})
            raise


async def list_tools(url: str, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
    async with mcp_session(url, correlation_id=correlation_id) as session:
        response = await session.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
            "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else {},
        }
        for t in response.tools
    ]
