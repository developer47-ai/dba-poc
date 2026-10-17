"""REST API of the simulated network backend."""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from network_simulator.engine import Network, NotApplicableError, UnknownCustomerError


class ActionRequest(BaseModel):
    target: str = "modem"


def create_app(network: Network) -> FastAPI:
    app = FastAPI(title="Network Simulator", version="0.1.0")
    app.state.network = network

    @app.exception_handler(UnknownCustomerError)
    async def _unknown(_: Request, exc: UnknownCustomerError):
        return JSONResponse(status_code=404, content={"detail": f"customer {exc} not found"})

    @app.exception_handler(NotApplicableError)
    async def _not_applicable(_: Request, exc: NotApplicableError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "customers": len(network.summaries())}

    @app.get("/customers")
    def customers() -> list[dict[str, Any]]:
        return network.summaries()

    @app.get("/customers/{customer_id}/installed-base")
    def installed_base(customer_id: str):
        return network.installed_base(customer_id)

    @app.get("/customers/{customer_id}/ppp-session")
    def ppp_session(customer_id: str):
        return network.ppp_session(customer_id)

    @app.get("/customers/{customer_id}/ppp-history")
    def ppp_history(customer_id: str):
        return network.ppp_history(customer_id)

    @app.get("/customers/{customer_id}/ont-status")
    def ont_status(customer_id: str):
        return network.ont_status(customer_id)

    @app.get("/customers/{customer_id}/access-port")
    def access_port(customer_id: str):
        return network.access_port(customer_id)

    @app.get("/customers/{customer_id}/led-status")
    def led_status(customer_id: str):
        return network.led_status(customer_id)

    @app.get("/customers/{customer_id}/device-details")
    def device_details(customer_id: str):
        return network.device_details(customer_id)

    @app.get("/customers/{customer_id}/wifi-settings")
    def wifi_settings(customer_id: str):
        return network.wifi_settings(customer_id)

    @app.get("/customers/{customer_id}/managed-devices")
    def managed_devices(customer_id: str):
        return network.managed_devices(customer_id)

    @app.get("/customers/{customer_id}/network-map")
    def network_map(customer_id: str):
        return network.network_map(customer_id)

    @app.post("/customers/{customer_id}/reboot-device")
    def reboot_device(customer_id: str, body: ActionRequest | None = None):
        return network.apply_action(customer_id, "reboot_device", (body or ActionRequest()).target)

    @app.post("/customers/{customer_id}/reset-wifi")
    def reset_wifi(customer_id: str):
        return network.apply_action(customer_id, "reset_wifi")

    @app.post("/customers/{customer_id}/factory-reset-device")
    def factory_reset_device(customer_id: str):
        return network.apply_action(customer_id, "factory_reset_device")

    # Demo/test helpers: not exposed through MCP.
    @app.post("/admin/reset")
    def reset_all(customer_id: str | None = None):
        try:
            network.reset(customer_id)
        except UnknownCustomerError as exc:
            raise HTTPException(404, f"customer {exc} not found") from exc
        return {"status": "reset", "customer_id": customer_id}

    return app
