"""DBA Expert Console - Streamlit chat UI. Run with: python -m chat_ui"""

import os

import streamlit as st

from chat_ui.client import OrchestratorClient, OrchestratorError
from chat_ui.view import TurnView, apply_event
from poc_common.events import EventType

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
TEXT_EVENTS = {EventType.AGENT_MESSAGE_DELTA, EventType.AGENT_MESSAGE}

st.set_page_config(page_title="NetCo · DBA Expert Console", page_icon="🛠️", layout="wide")


@st.cache_resource
def get_client(url: str) -> OrchestratorClient:
    return OrchestratorClient(url)


client = get_client(ORCHESTRATOR_URL)
state = st.session_state
state.setdefault("session", None)
state.setdefault("messages", [])


def customer_label(customer: dict) -> str:
    return (
        f"{customer['customer_id']} · {customer['name']} · "
        f"{customer['carrier_type']} {customer['carrier_tech_type']}"
    )


def render_activity(log: list[str]) -> None:
    if log:
        with st.expander(f"Agent activity ({len(log)} steps)"):
            for line in log:
                st.markdown(line)


# ---- sidebar: customer and session --------------------------------------------------------------
with st.sidebar:
    st.title("🛠️ DBA Expert Console")
    st.caption("NetCo connectivity troubleshooting · local multi-agent POC")
    try:
        customers = {c["customer_id"]: c for c in client.list_customers()}
    except OrchestratorError as exc:
        st.error(f"Orchestrator not reachable at {ORCHESTRATOR_URL}: {exc}")
        st.stop()

    customer_id = st.selectbox(
        "Scenario customer", list(customers), format_func=lambda cid: customer_label(customers[cid])
    )
    st.caption(customers[customer_id]["description"])
    expert_name = st.text_input("Your name", value="Support expert")

    if st.button("Start new session", type="primary", use_container_width=True):
        state.session = client.create_session(customer_id, expert_name)
        state.messages = []
    if st.button(
        "Reset customer's network", use_container_width=True,
        help="Restore the scenario's starting state (undo reboots and resets)",
    ):
        client.reset_customer(customer_id)
        st.toast(f"{customer_id} reset to its starting state")

    if state.session:
        st.divider()
        st.markdown(
            f"**Session** `{state.session['session_id'][:8]}`  \n"
            f"**Customer** {state.session['customer_id']}"
        )
        if state.session["customer_id"] != customer_id:
            st.warning("This session is for another customer. Start a new session to switch.")

# ---- conversation --------------------------------------------------------------------------------
st.header("Connectivity troubleshooting")
if not state.session:
    st.info("Pick a scenario customer in the sidebar and click **Start new session**.")
    st.stop()

for message in state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_activity(message.get("log", []))
        st.markdown(message["content"] or "_No answer._")
        if message.get("error"):
            st.error(message["error"])

if prompt := st.chat_input("Describe the customer's problem…"):
    state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        status = st.status("Routing…", expanded=True)
        answer = st.empty()
        view = TurnView()
        try:
            for event in client.stream_turn(state.session["session_id"], prompt):
                line = apply_event(view, event)
                if line:
                    status.markdown(line)
                status.update(label=view.status_label)
                if event.type in TEXT_EVENTS:
                    answer.markdown(view.text + ("" if view.final else " ▌"))
        except OrchestratorError as exc:
            view.error = str(exc)
        answer.markdown(view.text or "_No answer._")
        if view.error:
            st.error(view.error)
        status.update(
            label=view.summary_label, state="error" if view.error else "complete", expanded=False
        )

    state.messages.append(
        {"role": "assistant", "content": view.text, "log": view.log, "error": view.error}
    )
