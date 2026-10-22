from datetime import date

import httpx
import pytest

from poc_common.mcp_client import McpToolError, call_tool, list_tools
from poc_common.testing import run_app_in_thread
from scheduling_mcp import Scheduler, create_server

FRIDAY = date(2026, 10, 23)  # so the next working days are Monday and Tuesday


@pytest.fixture
def scheduler() -> Scheduler:
    return Scheduler(today=lambda: FRIDAY)


@pytest.fixture
def mcp_url(scheduler):
    with run_app_in_thread(create_server(scheduler).streamable_http_app()) as base:
        yield f"{base}/mcp"


def test_slots_are_morning_and_afternoon_on_working_days_only(scheduler):
    slots = scheduler.search(days_ahead=7, limit=10)
    assert [s["slot_id"] for s in slots[:4]] == [
        "SLT-20261026-AM", "SLT-20261026-PM", "SLT-20261027-AM", "SLT-20261027-PM"]
    assert slots[0]["weekday"] == "Monday" and slots[0]["from"] == "08:00"
    assert not any(s["weekday"] in ("Saturday", "Sunday") for s in slots)


def test_booking_takes_the_slot_off_the_list(scheduler):
    booking = scheduler.book("C1001", "SLT-20261026-AM")
    assert booking["status"] == "confirmed" and booking["booking_id"] == "APT-000001"
    assert "SLT-20261026-AM" not in [s["slot_id"] for s in scheduler.search(7, 10)]
    with pytest.raises(ValueError, match="not available"):
        scheduler.book("C1002", "SLT-20261026-AM")
    assert scheduler.for_customer("C1001") == [booking] and scheduler.for_customer("C1002") == []


@pytest.mark.parametrize("slot", ["nonsense", "SLT-20261025-AM", "SLT-20261224-AM"])
def test_invalid_or_unavailable_slots_are_refused(scheduler, slot):
    with pytest.raises(ValueError):
        scheduler.book("C1001", slot)


async def test_tools_over_mcp_with_consent_hints(mcp_url, scheduler):
    tools = {t["name"]: t for t in await list_tools(mcp_url)}
    assert set(tools) == {"search_appointment_slots", "book_appointment"}
    assert tools["search_appointment_slots"]["annotations"]["readOnlyHint"] is True
    assert tools["book_appointment"]["annotations"]["readOnlyHint"] is False

    found = await call_tool(mcp_url, "search_appointment_slots", {"customer_id": "C1001"})
    slot_id = found["slots"][0]["slot_id"]
    booked = await call_tool(mcp_url, "book_appointment",
                             {"customer_id": "C1001", "slot_id": slot_id})
    assert booked["slot"]["slot_id"] == slot_id
    with pytest.raises(McpToolError, match="not available"):
        await call_tool(mcp_url, "book_appointment", {"customer_id": "C1001", "slot_id": slot_id})


def test_http_routes(mcp_url, scheduler):
    base = mcp_url.removesuffix("/mcp")
    assert httpx.get(f"{base}/health").json() == {"status": "ok", "service": "scheduling-mcp"}
    scheduler.book("C1001", "SLT-20261026-PM")
    assert len(httpx.get(f"{base}/v1/records", params={"customer_id": "C1001"}).json()[
        "appointments"]) == 1
    assert httpx.post(f"{base}/admin/reset").json() == {"status": "reset"}
    assert httpx.get(f"{base}/v1/records").json() == {"appointments": []}
