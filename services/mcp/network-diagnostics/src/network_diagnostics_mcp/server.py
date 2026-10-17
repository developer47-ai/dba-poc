"""Network diagnostics MCP server.

Read tools only observe the network. State-changing tools carry `readOnlyHint=false`, which the
agents use to ask the support expert for approval before calling them (human-in-the-loop).
"""

import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from poc_common.config import get_config
from poc_common.logging import setup_logging

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
CHANGES_STATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


class Backend:
    """Calls the (simulated) network backend and turns HTTP errors into readable messages."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=30)

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"network backend unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ValueError(detail)
        return response.json()


def create_server(
    backend_url: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    host: str = "127.0.0.1",
    port: int = 8101,
) -> FastMCP:
    backend = Backend(backend_url or get_config().network_simulator_url, client)
    # Binding to 0.0.0.0 (containers) also disables FastMCP's localhost-only Host header check.
    server = FastMCP("network-diagnostics", host=host, port=port)

    @server.tool(annotations=READ_ONLY)
    async def get_ppp_session(customer_id: str) -> dict:
        """Current PPP (internet) session status of the customer: 'up' or 'down'."""
        return await backend.request("GET", f"/customers/{customer_id}/ppp-session")

    @server.tool(annotations=READ_ONLY)
    async def get_ppp_history(customer_id: str) -> list[dict]:
        """Recent PPP session changes (newest first) with the reason for each change."""
        return await backend.request("GET", f"/customers/{customer_id}/ppp-history")

    @server.tool(annotations=READ_ONLY)
    async def get_ont_status(customer_id: str) -> dict:
        """Optical status of the fiber ONT: OLT port, optical signal, rx power and ONT lights.
        Only for GPON/XGSPON fiber lines; fails for copper and point-to-point fiber."""
        return await backend.request("GET", f"/customers/{customer_id}/ont-status")

    @server.tool(annotations=READ_ONLY)
    async def get_access_port_status(customer_id: str) -> dict:
        """Status of the DSLAM port (copper) or point-to-point switch port (EoF/GoF), with line
        sync rates. Fails for GPON/XGSPON fiber lines, which have no such port."""
        return await backend.request("GET", f"/customers/{customer_id}/access-port")

    @server.tool(annotations=READ_ONLY)
    async def get_led_status(customer_id: str) -> dict:
        """Indicator light colours of the modem (power, wan, internet, wifi) and, if present,
        of the ONT (power, pon, los)."""
        return await backend.request("GET", f"/customers/{customer_id}/led-status")

    @server.tool(annotations=READ_ONLY)
    async def get_device_details(customer_id: str) -> dict:
        """Model, serial, firmware, uptime, reboot count and online state of the modem and ONT."""
        return await backend.request("GET", f"/customers/{customer_id}/device-details")

    @server.tool(annotations=READ_ONLY)
    async def get_wifi_settings(customer_id: str) -> dict:
        """Current Wi-Fi configuration (SSID, security, channel, hidden) and whether it matches
        the factory defaults printed on the modem sticker. Never returns the password."""
        return await backend.request("GET", f"/customers/{customer_id}/wifi-settings")

    @server.tool(annotations=READ_ONLY)
    async def get_managed_devices(customer_id: str) -> list[dict]:
        """Devices known to the modem (laptops, phones, TVs) with connection state and signal."""
        return await backend.request("GET", f"/customers/{customer_id}/managed-devices")

    @server.tool(annotations=READ_ONLY)
    async def get_inhome_network_map(customer_id: str) -> dict:
        """The in-home network from the internet uplink to the modem and connected clients."""
        return await backend.request("GET", f"/customers/{customer_id}/network-map")

    @server.tool(annotations=CHANGES_STATE)
    async def reboot_device(customer_id: str, device: str = "modem") -> dict:
        """Remotely reboot the customer's 'modem' or 'ont'. Interrupts the customer's internet
        for about a minute, so it needs the support expert's approval first."""
        return await backend.request(
            "POST", f"/customers/{customer_id}/reboot-device", json={"target": device}
        )

    @server.tool(annotations=CHANGES_STATE)
    async def reset_wifi(customer_id: str) -> dict:
        """Reset Wi-Fi to the factory values printed on the modem sticker. Customers must
        reconnect their devices with the sticker password; needs the expert's approval first."""
        return await backend.request("POST", f"/customers/{customer_id}/reset-wifi")

    @server.tool(annotations=DESTRUCTIVE)
    async def factory_reset_device(customer_id: str) -> dict:
        """Factory-reset the modem, erasing ALL custom settings. Last resort; needs the expert's
        approval first."""
        return await backend.request("POST", f"/customers/{customer_id}/factory-reset-device")

    return server


def main() -> None:
    config = get_config()
    setup_logging("network-diagnostics-mcp", config.log_level, config.log_json)
    port = int(os.getenv("MCP_PORT", "8101"))
    logger.info("starting network-diagnostics MCP on port %s", port)
    create_server(host=os.getenv("MCP_HOST", "0.0.0.0"), port=port).run(transport="streamable-http")
