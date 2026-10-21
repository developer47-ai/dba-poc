"""Drive the real Streamlit page headless (Streamlit AppTest) against a running orchestrator."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import chat_ui

APP = str(Path(chat_ui.__file__).with_name("app.py"))


@pytest.fixture
def app(orchestrator_url, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", orchestrator_url)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def all_markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_sidebar_lists_scenario_customers_and_asks_for_a_session(app):
    assert not app.exception
    options = app.sidebar.selectbox[0].options
    assert len(options) == 6 and options[1].startswith("C1002 · Bram Jansen")
    assert "Start new session" in app.info[0].value


def test_full_conversation_turn(app, answer):
    app.sidebar.selectbox[0].set_value("C1002")
    app.sidebar.button[0].click().run()
    assert app.session_state.session["customer_id"] == "C1002"

    app.chat_input[0].set_value("Customer has no internet").run()

    assert not app.exception
    text = all_markdown(app)
    assert "Customer has no internet" in text
    assert answer in text
    assert "🔧 Calling `get_ppp_session`" in text
    assert "✅ `get_ppp_session` → status=down" in text
    stored = app.session_state.messages
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["content"] == answer and stored[1]["error"] is None
    assert "📘 Following **SOP-IH-001** v1.0 — No internet while the modem is online" in text
    assert stored[1]["citations"] == ["SOP-IH-001 v1.0"]
    assert stored[1]["consent"]["action"] == "reboot_device"
    assert "Approval needed" in app.warning[0].value and "reboot_device" in app.warning[0].value
    assert any("Sources: SOP-IH-001 v1.0" in c.value for c in app.caption)


def test_unreachable_orchestrator_shows_an_error(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://127.0.0.1:9")
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert "Orchestrator not reachable" in at.sidebar.error[0].value


def start_turn_with_pending_approval(app):
    app.sidebar.selectbox[0].set_value("C1002")
    app.sidebar.button[0].click().run()
    app.chat_input[0].set_value("Customer has no internet").run()
    assert not app.exception


def test_pending_approval_shows_buttons_and_approving_continues_the_session(app):
    start_turn_with_pending_approval(app)

    assert app.session_state.pending_consent["action"] == "reboot_device"
    assert [b.label for b in app.button if (b.key or "").startswith(("approve", "decline"))] == [
        "✅ Approve", "🚫 Decline"]

    app.button(key="approve-c1").click().run()

    assert not app.exception
    messages = app.session_state.messages
    assert messages[-2] == {"role": "user", "content": "✅ Approved: `reboot_device`"}
    assert messages[-1]["role"] == "assistant" and messages[-1]["error"] is None


def test_declining_sends_a_decline_and_a_new_message_discards_the_pending_approval(app):
    start_turn_with_pending_approval(app)
    app.button(key="decline-c1").click().run()
    assert app.session_state.messages[-2]["content"] == "🚫 Declined: `reboot_device`"

    start_turn_with_pending_approval(app)
    app.chat_input[0].set_value("What does an orange light mean?").run()
    assert app.session_state.messages[-2]["content"] == "What does an orange light mean?"