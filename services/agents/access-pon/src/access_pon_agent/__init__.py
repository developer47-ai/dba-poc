"""Access-PON specialist agent for the DBA POC: fiber access (GPON / XGSPON) up to the ONT."""

from langchain_core.language_models import BaseChatModel

from poc_common.config import Config
from sop_agent import AgentSpec
from sop_agent import create_app as _create_app
from sop_agent import main as _main

SPEC = AgentSpec(
    agent_id="access-pon",
    source="access-pon-agent",
    title="Access-PON Agent",
    role=(
        "the Access-PON Agent of NetCo, a telecom operator, for fiber (GPON and XGSPON) "
        "access-network problems between the OLT in the network and the customer's ONT"
    ),
    domain_rules=(
        "- You cover the fiber access network up to the customer's ONT. If the ONT receives a "
        "good optical signal, the fault is inside the home: say so and recommend handing the "
        "case over to the In-Home agent."
    ),
    port_env="ACCESS_PON_PORT",
    default_port=8202,
)


def create_app(config: Config | None = None, llm: BaseChatModel | None = None):
    return _create_app(SPEC, config, llm)


def main() -> None:
    _main(SPEC)


__all__ = ["SPEC", "create_app", "main"]
