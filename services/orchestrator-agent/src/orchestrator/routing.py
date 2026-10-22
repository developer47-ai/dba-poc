"""Which specialist agent handles a turn, and where a case goes when an agent hands it over.

The first agent is always the In-Home agent. When a procedure ends with a hand-over, the
orchestrator picks the next agent: directly when the SOP names one, or by the customer's
carrier technology when it says "access-network".
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from orchestrator.sessions import Session
from poc_common.config import Config

INHOME = "inhome-agent"
ACCESS_PON = "access-pon-agent"

# SOP `handover_to` values that name an agent directly.
DIRECT_TARGETS = {"inhome": INHOME, "access-pon": ACCESS_PON}
# For "access-network" the carrier technology decides. EoF/GoF (P2P) and copper agents are not
# part of this demo, so those cases are reported as having no agent yet.
ACCESS_BY_TECH = {"GPON": ACCESS_PON, "XGSPON": ACCESS_PON}

CarrierLookup = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class RoutingDecision:
    agent: str
    reason: str


class AgentRegistry:
    def __init__(self, agents: dict[str, str]) -> None:
        self._agents = {name: url.rstrip("/") for name, url in agents.items()}

    @classmethod
    def from_config(cls, config: Config) -> "AgentRegistry":
        return cls({INHOME: config.inhome_agent_url, ACCESS_PON: config.access_pon_agent_url})

    def url(self, agent: str) -> str:
        return self._agents[agent]

    def names(self) -> list[str]:
        return list(self._agents)

    def __contains__(self, agent: object) -> bool:
        return agent in self._agents


class Router:
    def __init__(
        self,
        registry: AgentRegistry,
        default_agent: str = INHOME,
        carrier_tech: CarrierLookup | None = None,
    ) -> None:
        self.registry = registry
        self.default_agent = default_agent
        # In production the installed-base system answers this; the demo asks the simulator.
        self.carrier_tech = carrier_tech

    def route(self, session: Session, message: str) -> RoutingDecision:
        if session.active_agent:
            return RoutingDecision(session.active_agent, "follow-up in the ongoing diagnosis")
        return RoutingDecision(self.default_agent, "start with in-home diagnosis")

    async def resolve_handover(self, target: str, customer_id: str) -> tuple[str | None, str]:
        """The agent a hand-over goes to (None when there is none) and a short explanation."""
        if target in DIRECT_TARGETS:
            agent = DIRECT_TARGETS[target]
            if agent in self.registry:
                return agent, f"{target} procedure"
            return None, f"{agent} is not available"
        if target == "access-network":
            tech = await self.carrier_tech(customer_id) if self.carrier_tech else None
            agent = ACCESS_BY_TECH.get((tech or "").upper())
            if agent and agent in self.registry:
                return agent, f"{tech} fiber line"
            return None, f"no access agent in this demo for technology {tech or 'unknown'}"
        return None, f"unknown hand-over target '{target}'"