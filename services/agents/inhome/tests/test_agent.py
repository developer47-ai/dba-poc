from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from inhome_agent import build_graph
from inhome_agent.prompts import SYSTEM_PROMPT
from inhome_agent.tools import load_read_only_tools
from poc_common.fake_llm import ScriptedChatModel, tool_call_message


async def test_agent_calls_tools_then_answers_from_the_results(mcp_url):
    llm = ScriptedChatModel(
        script=[
            tool_call_message("get_ppp_session", call_id="c1"),
            tool_call_message("get_led_status", call_id="c2"),
            AIMessage("Findings: PPP down, internet light orange. Diagnosis: stuck modem."),
        ]
    )
    graph = build_graph(llm, await load_read_only_tools(mcp_url, "C1002"))

    result = await graph.ainvoke({"messages": [HumanMessage("Customer has no internet")]})

    messages = result["messages"]
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_messages] == ["get_ppp_session", "get_led_status"]
    assert '"status": "down"' in tool_messages[0].content
    assert '"internet": "orange"' in tool_messages[1].content
    assert messages[-1].content.startswith("Findings:")


async def test_model_always_sees_the_system_prompt_and_prior_tool_results(mcp_url):
    llm = ScriptedChatModel(
        script=[tool_call_message("get_ppp_session"), AIMessage("done")]
    )
    graph = build_graph(llm, await load_read_only_tools(mcp_url, "C1006"))

    await graph.ainvoke({"messages": [HumanMessage("check the line")]})

    first_call, second_call = llm.received
    assert isinstance(first_call[0], SystemMessage) and first_call[0].content == SYSTEM_PROMPT
    assert any(isinstance(m, ToolMessage) and '"up"' in m.content for m in second_call)


async def test_unknown_tool_name_does_not_crash_the_graph(mcp_url):
    llm = ScriptedChatModel(script=[tool_call_message("reboot_device"), AIMessage("cannot reboot")])
    graph = build_graph(llm, await load_read_only_tools(mcp_url, "C1002"))

    result = await graph.ainvoke({"messages": [HumanMessage("please reboot")]})

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert "reboot_device" in tool_message.content
