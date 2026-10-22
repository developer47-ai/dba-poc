"""What makes one SOP-guided specialist agent different from another."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str      # scope name in the SOP front matter and in knowledge filters, e.g. "inhome"
    source: str        # service name used as the event source, e.g. "inhome-agent"
    title: str         # for API docs
    role: str          # who the agent is, used in its prompts
    domain_rules: str  # extra rules for the exploratory (no-SOP) fallback
    port_env: str      # environment variable holding the service port
    default_port: int
