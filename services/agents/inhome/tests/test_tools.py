import json

from inhome_agent.tools import load_read_only_tools

READ_TOOLS = {
    "get_ppp_session", "get_ppp_history", "get_ont_status", "get_access_port_status",
    "get_led_status", "get_device_details", "get_wifi_settings", "get_managed_devices",
    "get_inhome_network_map",
}


async def test_only_read_only_tools_are_offered(mcp_url):
    tools = await load_read_only_tools(mcp_url, "C1002")
    names = {t.name for t in tools}
    assert names == READ_TOOLS
    assert not names & {"reboot_device", "reset_wifi", "factory_reset_device"}


async def test_customer_id_is_hidden_from_the_model(mcp_url):
    tools = {t.name: t for t in await load_read_only_tools(mcp_url, "C1002")}
    schema = tools["get_ppp_session"].args_schema
    assert "customer_id" not in schema.get("properties", {})
    assert "customer_id" not in schema.get("required", [])


async def test_tool_call_is_scoped_to_the_customer(mcp_url):
    down = {t.name: t for t in await load_read_only_tools(mcp_url, "C1002")}
    up = {t.name: t for t in await load_read_only_tools(mcp_url, "C1006")}
    assert json.loads(await down["get_ppp_session"].ainvoke({}))["status"] == "down"
    assert json.loads(await up["get_ppp_session"].ainvoke({}))["status"] == "up"


async def test_tool_errors_become_text_the_model_can_read(mcp_url):
    tools = {t.name: t for t in await load_read_only_tools(mcp_url, "C1004")}
    result = await tools["get_ont_status"].ainvoke({})
    assert result.startswith("Tool error:") and "no ONT" in result
