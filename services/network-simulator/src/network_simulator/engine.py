"""Stateful fake network: loads scenario customers and applies remote actions to their state."""

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class UnknownCustomerError(KeyError):
    """No scenario exists for this customer id."""


class NotApplicableError(Exception):
    """The requested data or action does not apply to this customer's technology."""


class Scenario(BaseModel):
    customer_id: str
    name: str
    description: str
    address: dict[str, str]
    carrier_type: str
    carrier_tech_type: str
    internet_service: dict[str, Any]
    equipment: dict[str, dict[str, str] | None]
    wifi_sticker: dict[str, Any]
    state: dict[str, Any]
    remedies: list[dict[str, Any]] = Field(default_factory=list)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Network:
    def __init__(self, scenarios: list[Scenario]) -> None:
        self._scenarios = {s.customer_id: s for s in scenarios}
        self._state: dict[str, dict[str, Any]] = {}
        self.reset()

    @classmethod
    def from_directory(cls, directory: str | Path) -> "Network":
        files = sorted(Path(directory).glob("*.yaml"))
        if not files:
            raise FileNotFoundError(f"no scenario files found in {directory}")
        scenarios = [Scenario.model_validate(yaml.safe_load(f.read_text("utf-8"))) for f in files]
        return cls(scenarios)

    def reset(self, customer_id: str | None = None) -> None:
        ids = [customer_id] if customer_id else list(self._scenarios)
        for cid in ids:
            self._state[cid] = copy.deepcopy(self.scenario(cid).state)

    def scenario(self, customer_id: str) -> Scenario:
        try:
            return self._scenarios[customer_id]
        except KeyError:
            raise UnknownCustomerError(customer_id) from None

    def state(self, customer_id: str) -> dict[str, Any]:
        self.scenario(customer_id)
        return self._state[customer_id]

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "customer_id": s.customer_id,
                "name": s.name,
                "carrier_type": s.carrier_type,
                "carrier_tech_type": s.carrier_tech_type,
                "description": s.description,
            }
            for s in self._scenarios.values()
        ]

    # ---- read side -------------------------------------------------------------------------

    def installed_base(self, customer_id: str) -> dict[str, Any]:
        s = self.scenario(customer_id)
        return {
            "customer_id": s.customer_id,
            "name": s.name,
            "address": s.address,
            "carrier_type": s.carrier_type,
            "carrier_tech_type": s.carrier_tech_type,
            "internet_service": s.internet_service,
            "equipment": s.equipment,
        }

    def ppp_session(self, customer_id: str) -> dict[str, Any]:
        ppp = self.state(customer_id)["ppp"]
        return {"status": ppp["status"], "last_change": ppp["last_change"]}

    def ppp_history(self, customer_id: str) -> list[dict[str, Any]]:
        return self.state(customer_id)["ppp"]["history"]

    def ont_status(self, customer_id: str) -> dict[str, Any]:
        st = self.state(customer_id)
        if st["ont"] is None:
            raise NotApplicableError("no ONT on this connection (not a GPON/XGSPON fiber line)")
        return {"olt_port_status": st["olt_port"]["status"], **st["ont"]}

    def access_port(self, customer_id: str) -> dict[str, Any]:
        port = self.state(customer_id)["access_port"]
        if port is None:
            raise NotApplicableError("no DSLAM/P2P access port on this connection (PON line)")
        return port

    def led_status(self, customer_id: str) -> dict[str, Any]:
        st = self.state(customer_id)
        return {"modem": st["modem"]["leds"], "ont": st["ont"]["leds"] if st["ont"] else None}

    def device_details(self, customer_id: str) -> dict[str, Any]:
        s, st = self.scenario(customer_id), self.state(customer_id)
        modem = {**(s.equipment["modem"] or {}), "online": st["modem"]["online"],
                 "uptime_s": st["modem"]["uptime_s"], "reboot_count": st["modem"]["reboot_count"]}
        ont = None
        if s.equipment.get("ont"):
            ont = {**s.equipment["ont"], "optical_signal": st["ont"]["optical_signal"]}
        return {"modem": modem, "ont": ont}

    def wifi_settings(self, customer_id: str) -> dict[str, Any]:
        s, wifi = self.scenario(customer_id), self.state(customer_id)["wifi"]
        sticker = s.wifi_sticker
        matches = all(wifi.get(k) == sticker[k] for k in ("ssid", "security", "channel", "enabled"))
        return {**wifi, "hidden": wifi.get("hidden", False), "matches_factory_defaults": matches}

    def managed_devices(self, customer_id: str) -> list[dict[str, Any]]:
        return self.state(customer_id)["devices"]

    def network_map(self, customer_id: str) -> dict[str, Any]:
        s, st = self.scenario(customer_id), self.state(customer_id)
        uplink = "ont" if st["ont"] else "access_port"
        return {
            "internet": {"ppp": st["ppp"]["status"]},
            uplink: self.ont_status(customer_id) if st["ont"] else self.access_port(customer_id),
            "modem": {**(s.equipment["modem"] or {}), "online": st["modem"]["online"]},
            "clients": [
                {"name": d["name"], "connected": d["connected"], "band": d["band"]}
                for d in st["devices"]
            ],
        }

    # ---- write side ------------------------------------------------------------------------

    def apply_action(self, customer_id: str, action: str, target: str = "modem") -> dict[str, Any]:
        s, st = self.scenario(customer_id), self.state(customer_id)
        if target == "ont" and st["ont"] is None:
            raise NotApplicableError("this connection has no ONT")
        if target not in ("modem", "ont"):
            raise NotApplicableError(f"unknown device '{target}'")

        modem = st["modem"]
        before_ppp = st["ppp"]["status"]
        if action in ("reboot_device", "factory_reset_device") and target == "modem":
            modem["reboot_count"] += 1
            if not modem["hardware_fault"]:
                modem["uptime_s"] = 15
        if action in ("reset_wifi", "factory_reset_device") and target == "modem":
            st["wifi"] = {**s.wifi_sticker}
            st["wifi"].pop("password", None)
            for device in st["devices"]:
                device["connected"] = True
                device["band"] = device["band"] or "5GHz"
                device["rssi_dbm"] = device["rssi_dbm"] if device["rssi_dbm"] is not None else -55
            if not modem["hardware_fault"]:
                modem["leds"]["wifi"] = "green"

        for remedy in s.remedies:
            if remedy["action"] == action and remedy.get("target", "modem") == target:
                deep_merge(st, remedy["patch"])

        if st["ppp"]["status"] != before_ppp:
            now = _now()
            st["ppp"]["last_change"] = now
            st["ppp"]["history"].insert(
                0, {"timestamp": now, "status": st["ppp"]["status"], "reason": f"after {action}"}
            )
        return {"accepted": True, "action": action, "target": target, "estimated_seconds": 60}
