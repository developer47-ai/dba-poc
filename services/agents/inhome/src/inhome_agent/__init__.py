"""In-Home specialist agent for the DBA POC: a SOP-guided agent for problems inside the home."""

from langchain_core.language_models import BaseChatModel

from poc_common.config import Config
from sop_agent import AgentSpec
from sop_agent import create_app as _create_app
from sop_agent import main as _main

SPEC = AgentSpec(
    agent_id="inhome",
    source="inhome-agent",
    title="In-Home Agent",
    role=(
        "the In-Home Agent of NetCo, a telecom operator, for problems inside the customer's "
        "home: the modem, its indicator lights, the Wi-Fi settings and the connected devices"
    ),
    domain_rules=(
        "- If the evidence points outside the home (for example no optical signal at the ONT, or "
        "the access port is down), say so and recommend escalating to the access network team."
    ),
    port_env="INHOME_PORT",
    default_port=8201,
)


def create_app(config: Config | None = None, llm: BaseChatModel | None = None):
    return _create_app(SPEC, config, llm)


def main() -> None:
    _main(SPEC)


__all__ = ["SPEC", "create_app", "main"]
