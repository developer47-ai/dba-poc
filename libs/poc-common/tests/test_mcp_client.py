"""Integration test: the client talks to a real FastMCP server over streamable HTTP."""

import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import Context, FastMCP

from poc_common.logging import set_correlation_id
from poc_common.mcp_client import McpToolError, call_tool, list_tools


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_url():
    server = FastMCP("test-server")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    def line_status(customer_id: str) -> dict:
        """Return a structured status."""
        return {"customer_id": customer_id, "ppp": "down", "los": True}

    @server.tool()
    def greet(name: str) -> str:
        """Return a plain string."""
        return f"hello {name}"

    @server.tool()
    def explode() -> str:
        """Always fails."""
        raise ValueError("backend unavailable")

    @server.tool()
    def echo_correlation(ctx: Context) -> str:
        """Return the correlation id header the server received."""
        return ctx.request_context.request.headers.get("x-correlation-id", "")

    port = _free_port()
    config = uvicorn.Config(
        server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not uvicorn_server.started and time.time() < deadline:
        time.sleep(0.05)
    assert uvicorn_server.started, "test MCP server did not start"
    yield f"http://127.0.0.1:{port}/mcp"
    uvicorn_server.should_exit = True
    thread.join(timeout=5)


async def test_list_tools(mcp_url):
    tools = await list_tools(mcp_url)
    names = {t["name"] for t in tools}
    assert {"add", "line_status", "greet", "explode"} <= names
    add = next(t for t in tools if t["name"] == "add")
    assert add["description"] == "Add two numbers."
    assert set(add["input_schema"]["properties"]) == {"a", "b"}


async def test_scalar_result_is_unwrapped(mcp_url):
    assert await call_tool(mcp_url, "add", {"a": 2, "b": 3}) == 5


async def test_structured_result_is_a_dict(mcp_url):
    result = await call_tool(mcp_url, "line_status", {"customer_id": "C1"})
    assert result == {"customer_id": "C1", "ppp": "down", "los": True}


async def test_string_result(mcp_url):
    assert await call_tool(mcp_url, "greet", {"name": "Ayush"}) == "hello Ayush"


async def test_tool_error_raises_mcp_tool_error(mcp_url):
    with pytest.raises(McpToolError) as exc:
        await call_tool(mcp_url, "explode")
    assert exc.value.tool == "explode"
    assert "backend unavailable" in str(exc.value)


async def test_correlation_id_reaches_the_server(mcp_url):
    set_correlation_id("trace-42")
    assert await call_tool(mcp_url, "echo_correlation") == "trace-42"


async def test_explicit_correlation_id_wins(mcp_url):
    set_correlation_id("ambient")
    assert await call_tool(mcp_url, "echo_correlation", correlation_id="explicit") == "explicit"
