"""DBA Expert Console - Streamlit chat UI. Run with: python -m chat_ui"""

import os

import streamlit as st

from chat_ui.client import OrchestratorClient, OrchestratorError
from chat_ui.view import TurnView, apply_event
from poc_common.events import EventType

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
JAEGER_UI_URL = os.getenv("JAEGER_UI_URL", "http://localhost:16686").rstrip("/")
TEXT_EVENTS = {EventType.AGENT_MESSAGE_DELTA, EventType.AGENT_MESSAGE}

st.set_page_config(page_title="NetCo · DBA Expert Console", page_icon="🛠️", layout="wide")


@st.cache_resource
def get_client(url: str) -> OrchestratorClient:
    return OrchestratorClient(url)


client = get_client(ORCHESTRATOR_URL)
state = st.session_state
state.setdefault("session", None)
state.setdefault("messages", [])
state.setdefault("pending_consent", None)


def customer_label(customer: dict) -> str:
    return (
        f"{customer['customer_id']} · {customer['name']} · "
        f"{customer['carrier_type']} {customer['carrier_tech_type']}"
    )


def restore_session(summary: dict) -> None:
    """Continue an earlier case: show its stored conversation and any approval still waiting."""
    state.session = summary
    state.messages = [{"role": t["role"], "content": t["content"]} for t in summary["history"]]
    state.pending_consent = summary.get("pending_consent")
    if state.pending_consent:
        last = next((m for m in reversed(state.messages) if m["role"] == "assistant"), None)
        if last is None:
            last = {"role": "assistant", "content": ""}
            state.messages.append(last)
        last["consent"] = state.pending_consent


def case_label(case: dict) -> str:
    when = case["updated_at"][:16].replace("T", " ")
    waiting = " · ⏳ approval pending" if case["awaiting_approval"] else ""
    question = case["first_question"] or "(no messages)"
    return f"{when} · {case['turns']} turn(s){waiting} · {question[:40]}"


def render_activity(log: list[str]) -> None:
    if log:
        with st.expander(f"Agent activity ({len(log)} steps)"):
            for line in log:
                st.markdown(line)


def render_extras(
    citations: list[str], consent: dict | None, trace_id: str | None = None
) -> None:
    if citations:
        st.caption("Sources: " + " · ".join(citations))
    if trace_id:
        st.caption(f"[🔍 Open this turn's trace in Jaeger]({JAEGER_UI_URL}/trace/{trace_id})")
    if consent:
        st.warning(
            f"🛑 **Approval needed** — `{consent['action']}` "
            f"({consent['sop_id']} › {consent['step_id']}, risk: {consent['risk']}).  \n"
            "The agent has **not** done this. Nothing changes for the customer until an expert "
            "approves it."
        )


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
        state.pending_consent = None
    try:
        earlier = [c for c in client.list_sessions(customer_id)
                   if not state.session or c["session_id"] != state.session["session_id"]]
    except OrchestratorError:
        earlier = []
    if earlier:
        chosen = st.selectbox(
            "Earlier cases of this customer", earlier, format_func=case_label,
            help="Cases are stored in Postgres, so they survive a restart.",
        )
        if st.button("Resume this case", use_container_width=True):
            restore_session(client.get_session(chosen["session_id"]))
            st.rerun()
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
        if message["role"] == "assistant":
            asked = message.get("consent")
            still_pending = bool(
                asked and state.pending_consent
                and state.pending_consent["consent_id"] == asked["consent_id"]
            )
            render_extras(
                message.get("citations", []), asked if still_pending else None,
                message.get("trace_id"),
            )
        if message.get("error"):
            st.error(message["error"])

# Approve / Decline for the approval request the agent is waiting on (human-in-the-loop).
decision: bool | None = None
pending = state.pending_consent
if pending:
    approve_col, decline_col, _ = st.columns([1, 1, 4])
    key = pending["consent_id"][:8]
    if approve_col.button("✅ Approve", type="primary", key=f"approve-{key}"):
        decision = True
    if decline_col.button("🚫 Decline", key=f"decline-{key}"):
        decision = False

prompt = st.chat_input("Describe the customer's problem…")

stream = None
if decision is not None:
    state.pending_consent = None
    verdict = "✅ Approved" if decision else "🚫 Declined"
    user_text = f"{verdict}: `{pending['action']}`"
    stream = client.stream_consent(state.session["session_id"], pending["consent_id"], decision)
elif prompt:
    state.pending_consent = None
    user_text = prompt
    stream = client.stream_turn(state.session["session_id"], prompt)

if stream is not None:
    state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        status = st.status("Routing…", expanded=True)
        answer = st.empty()
        view = TurnView()
        try:
            for event in stream:
                line = apply_event(view, event)
                if line:
                    status.markdown(line)
                status.update(label=view.status_label)
                if event.type in TEXT_EVENTS:
                    answer.markdown(view.full_text + ("" if view.final else " ▌"))
        except OrchestratorError as exc:
            view.error = str(exc)
        answer.markdown(view.full_text or "_No answer._")
        render_extras(view.citations, view.consent, view.trace_id)
        if view.error:
            st.error(view.error)
        status.update(
            label=view.summary_label, state="error" if view.error else "complete", expanded=False
        )

    state.messages.append({
        "role": "assistant", "content": view.full_text, "log": view.log, "error": view.error,
        "citations": view.citations, "consent": view.consent, "trace_id": view.trace_id,
    })
    state.pending_consent = view.consent
    if view.consent:
        st.rerun()  # redraw so the Approve / Decline buttons appear under this answer