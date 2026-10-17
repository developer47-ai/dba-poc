"""End-to-end: MCP client -> diagnostics MCP server -> REST -> network simulator."""

from pathlib import Path

import pytest

from network_diagnostics_mcp import create_server
from network_simulator import Network, create_app
from poc_common.mcp_client import McpToolError, call_tool, list_tools
from poc_common.testing import run_app_in_thread

SCENARIO_DIR = Path(__file__).resolve().parents[4] / "data" / "scenarios"

READ_TOOLS = {
    "get_ppp_session", "get_ppp_history", "get_ont_status", "get_access_port_status",
    "get_led_status", "get_device_details", "get_wifi_settings", "get_managed_devices",
    "get_inhome_network_map",
}
WRITE_TOOLS = {"reboot_device", "reset_wifi", "factory_reset_device"}


@pytest.fixture
def mcp_url():
    network = Network.from_directory(SCENARIO_DIR)
    with run_app_in_thread(create_app(network)) as simulator_url:
        mcp_app = create_server(simulator_url).streamable_http_app()
        with run_app_in_thread(mcp_app) as base:
            yield f"{base}/mcp"


async def test_tools_are_listed_with_consent_hints(mcp_url):
    tools = {t["name"]: t for t in await list_tools(mcp_url)}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name in READ_TOOLS:
        assert tools[name]["annotations"]["readOnlyHint"] is True
    for name in WRITE_TOOLS:
        assert tools[name]["annotations"]["readOnlyHint"] is False
    assert tools["factory_reset_device"]["annotations"]["destructiveHint"] is True
    assert tools["reboot_device"]["annotations"]["destructiveHint"] is False


async def test_read_tool_returns_structured_data(mcp_url):
    ppp = await call_tool(mcp_url, "get_ppp_session", {"customer_id": "C1002"})
    assert ppp["status"] == "down"
    history = await call_tool(mcp_url, "get_ppp_history", {"customer_id": "C1002"})
    assert isinstance(history, list) and history[0]["status"] == "down"


async def test_los_customer_diagnosis_path(mcp_url):
    ont = await call_tool(mcp_url, "get_ont_status", {"customer_id": "C1001"})
    assert ont["optical_signal"] is False and ont["leds"]["los"] == "red"


async def test_reboot_flow_changes_state_visible_through_read_tools(mcp_url):
    args = {"customer_id": "C1002"}
    assert (await call_tool(mcp_url, "get_ppp_session", args))["status"] == "down"
    result = await call_tool(mcp_url, "reboot_device", {**args, "device": "modem"})
    assert result["accepted"] is True
    assert (await call_tool(mcp_url, "get_ppp_session", args))["status"] == "up"


async def test_wifi_reset_flow(mcp_url):
    args = {"customer_id": "C1003"}
    before = await call_tool(mcp_url, "get_wifi_settings", args)
    assert before["matches_factory_defaults"] is False
    await call_tool(mcp_url, "reset_wifi", args)
    after = await call_tool(mcp_url, "get_wifi_settings", args)
    assert after["matches_factory_defaults"] is True


async def test_inapplicable_tool_is_a_readable_tool_error(mcp_url):
    with pytest.raises(McpToolError, match="no ONT"):
        await call_tool(mcp_url, "get_ont_status", {"customer_id": "C1004"})


async def test_unknown_customer_is_a_readable_tool_error(mcp_url):
    with pytest.raises(McpToolError, match="C9999"):
        await call_tool(mcp_url, "get_ppp_session", {"customer_id": "C9999"})
