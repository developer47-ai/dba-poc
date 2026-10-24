import time

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from orchestrator import create_app
from orchestrator.routing import AgentRegistry
from poc_common.config import Config
from poc_common.events import EventType, parse_sse
from poc_common.fake_llm import tool_call_message
from poc_common.testing import run_app_in_thread

STUCK_MODEM = "The customer has no internet and the modem's internet light is orange"


def choice(outcome: str) -> AIMessage:
    return tool_call_message("report_outcome", {"outcome": outcome, "reason": "scripted"})


def new_session(client: TestClient, customer_id: str = "C1002") -> str:
    response = client.post("/v1/sessions", json={"customer_id": customer_id, "expert_name": "Sam"})
    assert response.status_code == 201
    return response.json()["session_id"]


def send(client: TestClient, session_id: str, message: str, **headers):
    response = client.post(
        f"/v1/sessions/{session_id}/messages", json={"message": message}, headers=headers
    )
    return response, parse_sse(response.text) if response.status_code == 200 else []


def test_turn_streams_the_sop_guided_diagnosis_end_to_end(inhome, orchestrator):
    """Orchestrator -> In-Home agent -> knowledge MCP + diagnostics MCP -> simulator."""
    agent_url, _ = inhome(
        choice("troubleshoot"), choice("down"), choice("modem_stuck"),
        AIMessage("Findings: PPP down [SOP-IH-001 v1.0 › S1]. Next step: approve the reboot."),
    )
    client = orchestrator(agent_url)
    session_id = new_session(client)

    response, events = send(client, session_id, STUCK_MODEM, **{"X-Correlation-ID": "corr-9"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events[1].data == {
        "from_agent": "orchestrator", "to_agent": "inhome-agent",
        "reason": "start with in-home diagnosis",
    }
    steps = [e.data["step"] for e in events if e.type is EventType.STATUS_UPDATE]
    assert steps[:3] == ["routing", "diagnosis_started", "reasoning"]
    assert [e.data["step_id"] for e in events if e.data.get("step") == "sop_step"] == [
        "S1", "S2", "S3"]
    completed = [e.data for e in events if e.type is EventType.TOOL_CALL
                 and e.data["status"] == "completed"]
    assert [c["tool"] for c in completed] == ["get_ppp_session", "get_led_status"]
    assert completed[0]["result"]["status"] == "down"
    consent = next(e.data for e in events if e.type is EventType.CONSENT_REQUEST)
    assert consent["action"] == "reboot_device"
    deltas = [e.data["text"] for e in events if e.type is EventType.AGENT_MESSAGE_DELTA]
    assert len(deltas) > 1 and "".join(deltas).startswith("Findings: PPP down")

    assert EventType.AGENT_STATE not in [e.type for e in events], "internal state stays internal"
    assert [e.source for e in events[:2]] == ["orchestrator", "orchestrator"]
    assert {e.source for e in events[2:-1]} == {"inhome-agent"}
    assert events[-1].type is EventType.DONE and events[-1].source == "orchestrator"
    assert {e.session_id for e in events} == {session_id}
    assert {e.correlation_id for e in events} == {"corr-9"}
    summary = client.get(f"/v1/sessions/{session_id}").json()
    assert summary["agent_state"]["inhome-agent"]["sop"]["step_id"] == "S3"


def test_history_and_agent_state_are_kept_for_the_next_turn(fake_agent, orchestrator):
    saved = {"sop": {"doc_id": "SOP-IH-002", "step_id": "S7", "awaiting": "expert"}}
    agent_url, calls = fake_agent(state=saved)
    client = orchestrator(agent_url)
    session_id = new_session(client)

    send(client, session_id, "wifi problem")
    _, second = send(client, session_id, "the laptop works again")

    assert EventType.AGENT_HANDOFF not in [e.type for e in second]
    assert second[0].data["reason"] == "follow-up in the ongoing diagnosis"
    assert calls[0]["json"]["state"] is None
    assert calls[1]["json"]["state"] == saved
    assert calls[1]["json"]["history"] == [
        {"role": "user", "content": "wifi problem"},
        {"role": "assistant", "content": "late answer"},
    ]
    summary = client.get(f"/v1/sessions/{session_id}").json()
    assert [t["role"] for t in summary["history"]] == ["user", "assistant"] * 2
    assert summary["active_agent"] == "inhome-agent" and summary["busy"] is False


def test_agent_receives_the_session_context_and_correlation_id(fake_agent, orchestrator):
    agent_url, calls = fake_agent()
    client = orchestrator(agent_url)
    session_id = new_session(client, "C1003")

    send(client, session_id, "wifi problem", **{"X-Correlation-ID": "corr-1"})

    request = calls[0]
    assert request["json"] == {
        "customer_id": "C1003", "message": "wifi problem",
        "session_id": session_id, "history": [], "state": None, "consent": None,
    }
    assert request["headers"]["x-correlation-id"] == "corr-1"


def test_unreachable_agent_ends_the_turn_cleanly(orchestrator):
    client = orchestrator("http://127.0.0.1:9")
    session_id = new_session(client)

    _, events = send(client, session_id, "no internet")

    assert [e.type for e in events][-2:] == [EventType.ERROR, EventType.DONE]
    assert "not available" in events[-2].data["message"]
    summary = client.get(f"/v1/sessions/{session_id}").json()
    assert summary["history"] == [] and summary["busy"] is False


def test_agent_http_error_is_reported(fake_agent, orchestrator):
    agent_url, _ = fake_agent(status=500)
    client = orchestrator(agent_url)

    _, events = send(client, new_session(client), "no internet")

    assert events[-2].type is EventType.ERROR and "HTTP 500" in events[-2].data["message"]


def test_heartbeats_keep_a_slow_turn_alive(fake_agent, orchestrator):
    agent_url, _ = fake_agent(delay=0.6)
    client = orchestrator(agent_url, heartbeat_s=0.1)

    response, events = send(client, new_session(client), "no internet")

    assert ": heartbeat" in response.text
    assert events[-2].data["text"] == "late answer" and events[-1].type is EventType.DONE


def test_answer_tokens_reach_the_client_while_the_agent_is_still_working(fake_agent):
    """Real-time check over real HTTP: the first token must not wait for the whole answer."""
    agent_url, _ = fake_agent(delay=1.5)
    app = create_app(Config(), registry=AgentRegistry({"inhome-agent": agent_url}))
    with run_app_in_thread(app) as base:
        session_id = httpx.post(f"{base}/v1/sessions", json={"customer_id": "C1002"}).json()[
            "session_id"
        ]
        started = time.monotonic()
        arrivals: list[tuple[str, float]] = []
        with httpx.stream(
            "POST", f"{base}/v1/sessions/{session_id}/messages",
            json={"message": "no internet"}, timeout=10,
        ) as response:
            for line in response.iter_lines():
                if line.startswith("event: "):
                    arrivals.append((line[7:], time.monotonic() - started))

    first_token = next(t for name, t in arrivals if name == "agent_message_delta")
    done = next(t for name, t in arrivals if name == "done")
    assert first_token < 0.8, f"first token took {first_token:.2f}s"
    assert done >= 1.5


def test_second_turn_while_busy_is_rejected(fake_agent, orchestrator):
    agent_url, _ = fake_agent()
    client = orchestrator(agent_url)
    session_id = new_session(client)
    client.app.state.sessions.get(session_id).start_turn()

    response, _ = send(client, session_id, "hello?")

    assert response.status_code == 409


def test_unknown_session_and_invalid_input(fake_agent, orchestrator):
    agent_url, _ = fake_agent()
    client = orchestrator(agent_url)

    assert client.get("/v1/sessions/nope").status_code == 404
    assert send(client, "nope", "hi")[0].status_code == 404
    assert client.post("/v1/sessions", json={"customer_id": ""}).status_code == 422
    assert send(client, new_session(client), "")[0].status_code == 422


def test_health(fake_agent, orchestrator):
    agent_url, _ = fake_agent()
    assert orchestrator(agent_url).get("/health").json() == {
        "status": "ok", "service": "orchestrator", "agents": ["inhome-agent"],
    }


def test_demo_endpoints_list_and_reset_scenario_customers(simulator_url):
    app = create_app(Config(network_simulator_url=simulator_url),
                     registry=AgentRegistry({"inhome-agent": "http://unused"}))
    with TestClient(app) as client:
        customers = client.get("/v1/demo/customers").json()
        assert [c["customer_id"] for c in customers][:2] == ["C1001", "C1002"]
        assert client.post("/v1/demo/customers/C1002/reset").json()["status"] == "reset"
        assert client.post("/v1/demo/customers/C9999/reset").status_code == 404


def test_demo_endpoints_report_a_missing_simulator():
    app = create_app(Config(network_simulator_url="http://127.0.0.1:9"),
                     registry=AgentRegistry({"inhome-agent": "http://unused"}))
    with TestClient(app) as client:
        assert client.get("/v1/demo/customers").status_code == 503


REBOOT = {"consent_id": "c-123", "action": "reboot_device", "description": "Reboot the modem",
          "risk": "medium", "arguments": {"customer_id": "C1002"}, "sop_id": "SOP-IH-001",
          "step_id": "S3"}


def decide(client, session_id: str, consent_id: str, approved: bool, comment=None):
    response = client.post(f"/v1/sessions/{session_id}/consent", json={
        "consent_id": consent_id, "approved": approved, "comment": comment})
    return response, parse_sse(response.text) if response.status_code == 200 else []


def test_pending_approval_is_tracked_and_hidden_state_stays_internal(fake_agent, orchestrator):
    agent_url, _ = fake_agent(ask_consent=REBOOT)
    client = orchestrator(agent_url)
    session_id = new_session(client)

    _, events = send(client, session_id, "no internet")

    assert [e.data["action"] for e in events if e.type is EventType.CONSENT_REQUEST] == [
        "reboot_device"]
    summary = client.get(f"/v1/sessions/{session_id}").json()
    assert summary["pending_consent"]["consent_id"] == "c-123"
    assert summary["pending_consent"]["agent"] == "inhome-agent"
    assert summary["consent_log"] == []


def test_approval_reaches_the_agent_and_is_audited(fake_agent, orchestrator):
    agent_url, calls = fake_agent(ask_consent=REBOOT)
    client = orchestrator(agent_url)
    session_id = new_session(client)
    send(client, session_id, "no internet")

    response, events = decide(client, session_id, "c-123", True, "customer agreed")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events[-1].type is EventType.DONE
    sent = calls[1]["json"]
    assert sent["consent"] == {"consent_id": "c-123", "approved": True,
                               "comment": "customer agreed"}
    assert sent["message"] == "Approved: reboot_device (customer agreed)"
    assert sent["state"]["sop"]["awaiting"] == "consent"
    summary = client.get(f"/v1/sessions/{session_id}").json()
    assert summary["pending_consent"] is None
    [entry] = summary["consent_log"]
    assert entry["approved"] is True and entry["action"] == "reboot_device"
    assert entry["sop_id"] == "SOP-IH-001" and entry["step_id"] == "S3"
    assert entry["decided_by"] == "Sam" and entry["decided_at"]
    assert summary["history"][-2]["content"] == "Approved: reboot_device (customer agreed)"


def test_decline_is_audited_too(fake_agent, orchestrator):
    agent_url, calls = fake_agent(ask_consent=REBOOT)
    client = orchestrator(agent_url)
    session_id = new_session(client)
    send(client, session_id, "no internet")

    decide(client, session_id, "c-123", False)

    assert calls[1]["json"]["consent"]["approved"] is False
    assert calls[1]["json"]["message"] == "Declined: reboot_device"
    assert client.get(f"/v1/sessions/{session_id}").json()["consent_log"][0]["approved"] is False


def test_only_the_pending_request_can_be_decided(fake_agent, orchestrator):
    agent_url, calls = fake_agent(ask_consent=REBOOT)
    client = orchestrator(agent_url)
    session_id = new_session(client)

    assert decide(client, session_id, "c-123", True)[0].status_code == 409  # nothing pending yet
    send(client, session_id, "no internet")
    assert decide(client, session_id, "someone-elses", True)[0].status_code == 409
    assert len(calls) == 1, "a refused decision never reaches the agent"
    assert client.get(f"/v1/sessions/{session_id}").json()["consent_log"] == []


def test_a_decision_can_only_be_used_once(fake_agent, orchestrator):
    agent_url, calls = fake_agent(ask_consent=REBOOT)
    client = orchestrator(agent_url)
    session_id = new_session(client)
    send(client, session_id, "no internet")

    assert decide(client, session_id, "c-123", True)[0].status_code == 200
    assert decide(client, session_id, "c-123", True)[0].status_code == 409
    assert len(calls) == 2 and len(client.get(f"/v1/sessions/{session_id}").json()[
        "consent_log"]) == 1


def test_decision_while_a_turn_runs_is_rejected_and_unknown_session_is_404(
        fake_agent, orchestrator):
    agent_url, _ = fake_agent(ask_consent=REBOOT)
    client = orchestrator(agent_url)
    session_id = new_session(client)
    send(client, session_id, "no internet")
    client.app.state.sessions.get(session_id).start_turn()

    assert decide(client, session_id, "c-123", True)[0].status_code == 409
    assert decide(client, "nope", "c-123", True)[0].status_code == 404
    bad = client.post(f"/v1/sessions/{session_id}/consent", json={"approved": True})
    assert bad.status_code == 422

HANDOVER = {
    "to": "access-network",
    "from": "SOP-IH-001 v1.0 › S9",
    "context": "ONT shows LOS, home network healthy",
}


def test_handover_goes_to_the_agent_for_the_customers_technology(
    fake_agent, orchestrator_with_agents
):
    inhome_url, inhome_calls = fake_agent(state={"sop": None, "handover": HANDOVER})
    pon_url, pon_calls = fake_agent()
    client = orchestrator_with_agents(
        {"inhome-agent": inhome_url, "access-pon-agent": pon_url}, carrier_tech="GPON"
    )
    session_id = new_session(client, "C1001")

    _, events = send(client, session_id, "no internet at all")

    handoffs = [e.data for e in events if e.type is EventType.AGENT_HANDOFF]
    assert [(h["from_agent"], h["to_agent"]) for h in handoffs] == [
        ("orchestrator", "inhome-agent"), ("inhome-agent", "access-pon-agent"),
    ]
    assert handoffs[1]["context"] == HANDOVER["context"]
    assert "GPON" in handoffs[1]["reason"]
    assert events[-1].type is EventType.DONE
    assert len(inhome_calls) == 1 and len(pon_calls) == 1
    passed_on = pon_calls[0]["json"]["message"]
    assert HANDOVER["context"] in passed_on and "no internet at all" in passed_on
    assert pon_calls[0]["json"]["state"] is None
    # The hand-over note is internal: it is not stored as the In-Home agent's saved state.
    summary = client.get(f"/v1/sessions/{session_id}").json()
    assert summary["active_agent"] == "access-pon-agent"
    assert [t["role"] for t in summary["history"]] == ["user", "assistant"]
    assert summary["history"][1]["content"].startswith("[inhome-agent] late answer")
    assert "[access-pon-agent] late answer" in summary["history"][1]["content"]


def test_follow_up_after_a_handover_goes_straight_to_the_new_agent(
    fake_agent, orchestrator_with_agents
):
    inhome_url, inhome_calls = fake_agent(state={"sop": None, "handover": HANDOVER})
    pon_url, pon_calls = fake_agent(state={"sop": {"doc_id": "SOP-PON-001", "awaiting": "expert"}})
    client = orchestrator_with_agents(
        {"inhome-agent": inhome_url, "access-pon-agent": pon_url}, carrier_tech="XGSPON"
    )
    session_id = new_session(client, "C1001")

    send(client, session_id, "no internet at all")
    _, second = send(client, session_id, "yes, the customer agrees")

    assert len(inhome_calls) == 1 and len(pon_calls) == 2
    assert pon_calls[1]["json"]["state"]["sop"]["doc_id"] == "SOP-PON-001"
    assert pon_calls[1]["json"]["message"] == "yes, the customer agrees"
    assert second[0].data["agent"] == "access-pon-agent"


@pytest.mark.parametrize("tech", ["EOF", "COPPER", None])
def test_handover_without_an_agent_for_the_technology_is_reported(
    fake_agent, orchestrator_with_agents, tech
):
    inhome_url, _ = fake_agent(state={"sop": None, "handover": HANDOVER})
    pon_url, pon_calls = fake_agent()
    client = orchestrator_with_agents(
        {"inhome-agent": inhome_url, "access-pon-agent": pon_url}, carrier_tech=tech
    )
    session_id = new_session(client, "C1005")

    _, events = send(client, session_id, "no internet")

    missing = [e.data for e in events if e.data.get("step") == "handover_unavailable"]
    assert len(missing) == 1 and "no access agent" in missing[0]["reason"]
    assert pon_calls == []
    assert events[-1].type is EventType.DONE
    assert client.get(f"/v1/sessions/{session_id}").json()["active_agent"] == "inhome-agent"


def test_direct_handover_target_and_hop_limit(fake_agent, orchestrator_with_agents):
    ping = {"to": "access-pon", "from": "SOP-X", "context": "x"}
    pong = {"to": "inhome", "from": "SOP-Y", "context": "y"}
    inhome_url, inhome_calls = fake_agent(state={"sop": None, "handover": ping})
    pon_url, pon_calls = fake_agent(state={"sop": None, "handover": pong})
    client = orchestrator_with_agents({"inhome-agent": inhome_url, "access-pon-agent": pon_url})
    session_id = new_session(client)

    _, events = send(client, session_id, "loop")

    # in-home -> pon -> in-home, then the hop limit stops the ping-pong.
    assert (len(inhome_calls), len(pon_calls)) == (2, 1)
    assert [e.data["step"] for e in events if e.data.get("step") == "handover_unavailable"] \
        == ["handover_unavailable"]
    assert events[-1].type is EventType.DONE


def test_consent_after_a_handover_is_sent_to_the_agent_that_asked(
    fake_agent, orchestrator_with_agents
):
    ask = {"consent_id": "c-1", "action": "scheduling.book_appointment",
           "sop_id": "SOP-PON-001", "step_id": "S6", "arguments": {}}
    inhome_url, inhome_calls = fake_agent(state={"sop": None, "handover": HANDOVER})
    pon_url, pon_calls = fake_agent(ask_consent=ask)
    client = orchestrator_with_agents(
        {"inhome-agent": inhome_url, "access-pon-agent": pon_url}, carrier_tech="GPON"
    )
    session_id = new_session(client, "C1001")
    send(client, session_id, "no internet")

    response = client.post(
        f"/v1/sessions/{session_id}/consent",
        json={"consent_id": "c-1", "approved": True, "expert_name": "Sam"},
    )

    assert response.status_code == 200
    assert len(inhome_calls) == 1 and len(pon_calls) == 2
    assert pon_calls[1]["json"]["consent"]["approved"] is True

def test_a_turn_is_one_trace_that_reaches_both_agents(
    spans, fake_agent, orchestrator_with_agents
):
    inhome_url, inhome_calls = fake_agent(state={"sop": None, "handover": HANDOVER})
    pon_url, pon_calls = fake_agent()
    client = orchestrator_with_agents(
        {"inhome-agent": inhome_url, "access-pon-agent": pon_url}, carrier_tech="GPON"
    )
    session_id = new_session(client, "C1001")

    _, events = send(client, session_id, "no internet at all")

    trace_id = next(e.data["trace_id"] for e in events if e.data.get("step") == "trace")
    finished = spans.get_finished_spans()
    (turn,) = [s for s in finished if s.name == "turn"]
    assert format(turn.context.trace_id, "032x") == trace_id
    assert turn.attributes["session.id"] == session_id and turn.attributes["customer.id"] == "C1001"
    agent_calls = [s for s in finished if s.name == "agent.call"]
    assert [s.attributes["agent.name"] for s in agent_calls] == ["inhome-agent", "access-pon-agent"]
    assert all(s.parent.span_id == turn.context.span_id for s in agent_calls)
    server = [s for s in finished if s.name.startswith("POST /v1/sessions/{session_id}/messages")]
    assert server and server[0].context.trace_id == turn.context.trace_id
    # The trace context travels in the HTTP request, so each agent continues the same trace.
    for calls in (inhome_calls, pon_calls):
        assert trace_id in calls[0]["headers"]["traceparent"]


def test_an_unreachable_agent_marks_the_call_span_as_failed(spans, orchestrator):
    client = orchestrator("http://127.0.0.1:9")
    session_id = new_session(client)

    send(client, session_id, "no internet")

    (call,) = [s for s in spans.get_finished_spans() if s.name == "agent.call"]
    assert call.status.status_code.name == "ERROR"


def test_there_is_no_trace_event_when_tracing_is_off(fake_agent, orchestrator):
    agent_url, _ = fake_agent()
    client = orchestrator(agent_url)
    _, events = send(client, new_session(client), "hello")
    assert not [e for e in events if e.data.get("step") == "trace"]