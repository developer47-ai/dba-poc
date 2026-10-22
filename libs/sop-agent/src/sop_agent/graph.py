"""A specialist agent as a LangGraph: model node -> tool node -> model node, until it answers."""

from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build_graph(llm: BaseChatModel, tools: list[BaseTool], system_prompt: str):
    model = llm.bind_tools(tools)

    async def call_model(state: AgentState) -> dict:
        reply = await model.ainvoke([SystemMessage(system_prompt), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
