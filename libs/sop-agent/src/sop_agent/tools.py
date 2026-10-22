"""Load MCP tools as LangChain tools, scoped to one customer."""

import copy
import json
from typing import Any

from langchain_core.tools import StructuredTool

from poc_common.mcp_client import McpToolError, call_tool, list_tools

CUSTOMER_ARG = "customer_id"


def _hide_customer_id(schema: dict[str, Any]) -> dict[str, Any]:
    """The code supplies the customer id; hiding it stops small models from getting it wrong."""
    schema = copy.deepcopy(schema)
    schema.get("properties", {}).pop(CUSTOMER_ARG, None)
    if CUSTOMER_ARG in schema.get("required", []):
        schema["required"].remove(CUSTOMER_ARG)
    return schema


def _make_tool(mcp_url: str, customer_id: str, spec: dict[str, Any]) -> StructuredTool:
    name = spec["name"]

    async def run(**arguments: Any) -> str:
        try:
            result = await call_tool(mcp_url, name, {**arguments, CUSTOMER_ARG: customer_id})
        except McpToolError as exc:
            # Returned as text so the model can adapt (e.g. pick another tool) instead of crashing.
            return f"Tool error: {exc}"
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool(
        name=name,
        description=spec["description"],
        args_schema=_hide_customer_id(spec["input_schema"]),
        coroutine=run,
    )


async def load_read_only_tools(mcp_url: str, customer_id: str) -> list[StructuredTool]:
    """Only tools that do not change the network. State-changing tools need human approval and
    are added with the approval flow in a later step."""
    specs = await list_tools(mcp_url)
    return [
        _make_tool(mcp_url, customer_id, spec)
        for spec in specs
        if spec["annotations"].get("readOnlyHint")
    ]
