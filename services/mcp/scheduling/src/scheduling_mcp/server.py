"""Scheduling MCP server (stub): engineer appointment slots, kept in memory.

Slots are morning (08:00-12:00) and afternoon (13:00-17:00) windows on the coming working days.
Booking changes the customer's schedule, so agents book only after the support expert approves.
"""

import itertools
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from poc_common.config import get_config
from poc_common.logging import setup_logging

logger = logging.getLogger(__name__)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
CHANGES_STATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
WINDOWS = {"AM": ("08:00", "12:00"), "PM": ("13:00", "17:00")}
SLOT_ID = re.compile(r"^SLT-(\d{8})-(AM|PM)$")
HORIZON_DAYS = 14


class Scheduler:
    def __init__(self, today: Callable[[], date] | None = None) -> None:
        self._today = today or (lambda: datetime.now(UTC).date())
        self._ids = itertools.count(1)
        self.bookings: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.bookings.clear()
        self._ids = itertools.count(1)

    @staticmethod
    def _slot(day: date, window: str) -> dict[str, Any]:
        start, end = WINDOWS[window]
        return {"slot_id": f"SLT-{day:%Y%m%d}-{window}", "date": day.isoformat(),
                "weekday": day.strftime("%A"), "window": window, "from": start, "to": end}

    def _working_days(self, days: int) -> list[date]:
        first = self._today() + timedelta(days=1)
        return [d for d in (first + timedelta(days=n) for n in range(days)) if d.weekday() < 5]

    def _free(self, days: int) -> list[dict[str, Any]]:
        taken = {b["slot"]["slot_id"] for b in self.bookings}
        slots = [self._slot(d, w) for d in self._working_days(days) for w in WINDOWS]
        return [s for s in slots if s["slot_id"] not in taken]

    def search(self, days_ahead: int = 7, limit: int = 4) -> list[dict[str, Any]]:
        return self._free(max(1, min(days_ahead, HORIZON_DAYS)))[: max(1, limit)]

    def book(self, customer_id: str, slot_id: str) -> dict[str, Any]:
        if not customer_id.strip():
            raise ValueError("customer_id is required")
        match = SLOT_ID.match(slot_id.strip())
        if not match:
            raise ValueError(f"unknown slot '{slot_id}'; use a slot_id from the search result")
        slot = next((s for s in self._free(HORIZON_DAYS) if s["slot_id"] == slot_id.strip()), None)
        if slot is None:
            raise ValueError(f"slot {slot_id} is not available (taken, past or too far ahead)")
        booking = {
            "booking_id": f"APT-{next(self._ids):06d}", "customer_id": customer_id.strip(),
            "slot": slot, "status": "confirmed",
            "note": "An engineer visits within the time window; someone must be at home.",
        }
        self.bookings.append(booking)
        return booking

    def for_customer(self, customer_id: str | None) -> list[dict[str, Any]]:
        return [b for b in self.bookings if customer_id in (None, b["customer_id"])]


def create_server(
    scheduler: Scheduler | None = None, *, host: str = "127.0.0.1", port: int = 8103
) -> FastMCP:
    scheduler = scheduler or Scheduler()
    # Binding to 0.0.0.0 (containers) also disables FastMCP's localhost-only Host header check.
    server = FastMCP("scheduling", host=host, port=port)

    @server.tool(annotations=READ_ONLY)
    async def search_appointment_slots(
        customer_id: str, days_ahead: int = 7, limit: int = 4
    ) -> dict:
        """Free engineer appointment slots on the coming working days, earliest first. Each slot
        has a `slot_id` to book, a date and a time window."""
        return {"customer_id": customer_id, "slots": scheduler.search(days_ahead, limit)}

    @server.tool(annotations=CHANGES_STATE)
    async def book_appointment(customer_id: str, slot_id: str) -> dict:
        """Book an engineer visit in a slot returned by search_appointment_slots. Needs the
        support expert's approval first."""
        return scheduler.book(customer_id, slot_id)

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "scheduling-mcp"})

    @server.custom_route("/v1/records", methods=["GET"])
    async def records(request: Request) -> JSONResponse:
        customer = request.query_params.get("customer_id")
        return JSONResponse({"appointments": scheduler.for_customer(customer)})

    @server.custom_route("/admin/reset", methods=["POST"])
    async def reset(_: Request) -> JSONResponse:
        scheduler.reset()
        return JSONResponse({"status": "reset"})

    return server


def main() -> None:
    config = get_config()
    setup_logging("scheduling-mcp", config.log_level, config.log_json)
    port = int(os.getenv("MCP_PORT", "8103"))
    logger.info("starting scheduling MCP on port %s", port)
    create_server(host=os.getenv("MCP_HOST", "0.0.0.0"), port=port).run(
        transport="streamable-http"
    )
