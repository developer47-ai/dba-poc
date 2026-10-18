"""Which specialist agent handles a turn.

Step 5 has one specialist. Carrier-aware routing (fiber PON / P2P / copper) and hand-over between
agents plug in here once the installed base and the Access agents exist.
"""

from dataclasses import dataclass

from orchestrator.sessions import Session
from poc_common.config import Config

INHOME = "inhome-agent"


@dataclass(frozen=True)
class RoutingDecision:
    agent: str
    reason: str


class AgentRegistry:
    def __init__(self, agents: dict[str, str]) -> None:
        self._agents = {name: url.rstrip("/") for name, url in agents.items()}

    @classmethod
    def from_config(cls, config: Config) -> "AgentRegistry":
        return cls({INHOME: config.inhome_agent_url})

    def url(self, agent: str) -> str:
        return self._agents[agent]

    def names(self) -> list[str]:
        return list(self._agents)


class Router:
    def __init__(self, registry: AgentRegistry, default_agent: str = INHOME) -> None:
        self.registry = registry
        self.default_agent = default_agent

    def route(self, session: Session, message: str) -> RoutingDecision:
        if session.active_agent:
            return RoutingDecision(session.active_agent, "follow-up in the ongoing diagnosis")
        return RoutingDecision(self.default_agent, "start with in-home diagnosis")
