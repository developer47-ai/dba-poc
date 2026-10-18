"""Tools an SOP step may name, and whether they change the customer's service.

A step that uses a state-changing tool must set `requires_consent: true`; the validator enforces
it, so no procedure can make an agent act without the support expert's approval.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolInfo:
    server: str
    name: str
    changes_state: bool

    @property
    def qualified_name(self) -> str:
        return f"{self.server}.{self.name}"


_TOOLS = [
    # network-diagnostics MCP (available now)
    ToolInfo("network-diagnostics", "get_ppp_session", False),
    ToolInfo("network-diagnostics", "get_ppp_history", False),
    ToolInfo("network-diagnostics", "get_ont_status", False),
    ToolInfo("network-diagnostics", "get_access_port_status", False),
    ToolInfo("network-diagnostics", "get_led_status", False),
    ToolInfo("network-diagnostics", "get_device_details", False),
    ToolInfo("network-diagnostics", "get_wifi_settings", False),
    ToolInfo("network-diagnostics", "get_managed_devices", False),
    ToolInfo("network-diagnostics", "get_inhome_network_map", False),
    ToolInfo("network-diagnostics", "reboot_device", True),
    ToolInfo("network-diagnostics", "reset_wifi", True),
    ToolInfo("network-diagnostics", "factory_reset_device", True),
    # planned MCP servers (later steps)
    ToolInfo("installed-base", "get_installed_base", False),
    ToolInfo("scheduling", "search_appointment_slots", False),
    ToolInfo("scheduling", "book_appointment", True),
    ToolInfo("ticketing", "create_incident", True),
    ToolInfo("ticketing", "create_swap_order", True),
]

TOOL_CATALOG: dict[str, ToolInfo] = {t.qualified_name: t for t in _TOOLS}

AGENTS = {"orchestrator", "inhome", "access-pon", "access-p2p", "access-copper"}
HANDOVER_TARGETS = AGENTS | {"access-network"}
