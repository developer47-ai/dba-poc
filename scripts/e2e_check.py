#!/usr/bin/env python3
"""End-to-end check of the running product (docker compose up -d), using only the stdlib.

1. Knowledge base: realistic questions must find the right SOP through knowledge-mcp (real bge-m3
   embeddings + pgvector hybrid search).
2. Conversation: resets scenario customer C1002 (stuck modem), opens a session and runs one
   troubleshooting turn through orchestrator -> In-Home agent -> MCP -> simulator with the real
   local LLM. Passes when the answer streams token by token, a diagnostic tool ran, the expert's
   approval is requested and the approved action runs.
3. Hand-over: customer C1001 (fiber, no light). The In-Home agent finds the problem is outside the
   home and hands over to the Access-PON agent, which follows SOP-PON-001 and asks approval to
   book an engineer; the approved booking must be confirmed by the scheduling MCP.
4. Document processing: a Word file and a PDF are converted by the Docling container, become
   searchable reference documents, and a chat question about the Word guide must cite it.
5. Memory: after the orchestrator container is restarted (E2E_RESTART=1), the hand-over case must
   still be there, complete, and listed among the customer's earlier cases.
6. Observability: that hand-over turn must be one distributed trace in Jaeger that spans the
   orchestrator and both agents, with SOP steps, LLM calls and MCP tool calls.

    python scripts/e2e_check.py   # ORCHESTRATOR_URL / CHAT_UI_URL / KNOWLEDGE_URL override defaults
"""

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlparse

ORCHESTRATOR = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000").rstrip("/")
CHAT_UI = os.getenv("CHAT_UI_URL", "http://localhost:8501").rstrip("/")
KNOWLEDGE = os.getenv("KNOWLEDGE_URL", "http://localhost:8105").rstrip("/")
JAEGER = os.getenv("JAEGER_URL", "http://localhost:16686").rstrip("/")
DOCLING = os.getenv("DOCLING_URL", "http://localhost:5001").rstrip("/")
DOCUMENTS = Path(__file__).resolve().parents[1] / "data" / "documents"
WAIT_S = int(os.getenv("E2E_WAIT_S", "900"))
CUSTOMER = os.getenv("E2E_CUSTOMER", "C1002")
MESSAGE = "The customer reports they have no internet. Please diagnose the connection."
ATTEMPTS = 2

# (question as an expert would type it, agent, acceptable top documents)
KNOWLEDGE_CHECKS = [
    ("There is no light from the fiber network and the LOS light is red", "access-pon",
     {"SOP-PON-001"}),
    ("The internet light on the modem is orange", "inhome", {"SOP-IH-001", "KB-LED-001"}),
    ("The customer changed the Wi-Fi password and now the laptop cannot connect", "inhome",
     {"SOP-IH-002", "KB-WIFI-001"}),
    ("The modem's power light is orange, it seems broken", "inhome",
     {"SOP-IH-003", "KB-LED-001", "SOP-IH-001"}),
]
OFF_TOPIC = ("How do I bake sourdough bread?", "inhome")


def request_json(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read() or b"null")


def wait_for(name: str, url: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5):
                print(f"[ready] {name}")
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if time.monotonic() > deadline:
                sys.exit(f"[fail] {name} not ready after {WAIT_S}s ({url})")
            time.sleep(3)


def stream_post(path: str, body: dict):
    """POST to the orchestrator and yield (event_type, payload, seconds_since_start) as frames
    arrive."""
    target = urlparse(ORCHESTRATOR)
    conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=900)
    started = time.monotonic()
    conn.request("POST", path, body=json.dumps(body),
                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    response = conn.getresponse()
    if response.status != 200:
        sys.exit(f"[fail] {path} returned HTTP {response.status}: {response.read()[:300]!r}")
    data: list[str] = []
    while line := response.readline():
        text = line.decode("utf-8").rstrip("\r\n")
        if text.startswith("data:"):
            data.append(text[5:].lstrip())
        elif text == "" and data:
            payload = json.loads("\n".join(data))
            data = []
            yield payload["type"], payload.get("data", {}), time.monotonic() - started
    conn.close()


def run_attempt(attempt: int, approve: bool = True) -> tuple[list[str], dict]:
    request_json("POST", f"{ORCHESTRATOR}/v1/demo/customers/{CUSTOMER}/reset")
    session = request_json("POST", f"{ORCHESTRATOR}/v1/sessions",
                           {"customer_id": CUSTOMER, "expert_name": "e2e"})
    print(f"\n[turn {attempt}] session {session['session_id'][:8]} · customer {CUSTOMER}")
    print(f"[expert] {MESSAGE}")

    tools, deltas, final, errors = [], [], "", []
    procedures, path, consent = [], [], None
    timing: dict[str, float] = {}
    path_ = f"/v1/sessions/{session['session_id']}/messages"
    for kind, data, at in stream_post(path_, {"message": MESSAGE}):
        timing.setdefault("first_event", at)
        step = data.get("step")
        if kind == "tool_call" and data.get("status") == "started":
            timing.setdefault("first_tool", at)
            tools.append(data["tool"])
            print(f"\n  [{at:6.1f}s] tool  {data['tool']}", flush=True)
        elif kind == "status_update" and step == "sop_selected":
            procedures.append(data["doc_id"])
            print(f"  [{at:6.1f}s] SOP   {data['doc_id']} — {data.get('title')}", flush=True)
        elif kind == "status_update" and step == "sop_outcome":
            path.append(f"{data['step_id']}={data.get('outcome')}")
            print(f"  [{at:6.1f}s] step  {data['step_id']} -> {data.get('outcome')} "
                  f"({data.get('reason', '')})", flush=True)
        elif kind == "consent_request":
            consent = data
            print(f"  [{at:6.1f}s] APPROVAL NEEDED {data.get('action')} "
                  f"({data.get('sop_id')} › {data.get('step_id')})", flush=True)
        elif kind == "agent_handoff":
            print(f"  [{at:6.1f}s] handoff -> {data.get('to_agent')}", flush=True)
        elif kind == "agent_message_delta":
            if "first_token" not in timing:
                timing["first_token"] = at
                print(f"  [{at:6.1f}s] answer: ", end="", flush=True)
            deltas.append(data.get("text", ""))
            print(data.get("text", ""), end="", flush=True)
        elif kind == "agent_message" and data.get("final"):
            final = data.get("text", "")
        elif kind == "error":
            errors.append(data.get("message", ""))
            print(f"\n  [{at:6.1f}s] ERROR {data.get('message')}", flush=True)
        elif kind == "done":
            timing["total"] = at
            print(f"\n  [{at:6.1f}s] done", flush=True)

    problems = []
    if errors:
        problems.append(f"error events: {errors}")
    if not tools:
        problems.append("the agent called no diagnostic tool")
    if len(deltas) < 2:
        problems.append(f"answer was not streamed ({len(deltas)} delta events)")
    if not final.strip():
        problems.append("no final answer")
    # C1002's modem is stuck: the SOP must lead to a remote reboot, which needs approval.
    if procedures[:1] != ["SOP-IH-001"]:
        problems.append(f"expected SOP-IH-001 first, followed {procedures}")
    if not consent or consent.get("action") != "reboot_device":
        problems.append(f"expected an approval request for reboot_device, got {consent}")
    metrics = {**timing, "tools": tools, "deltas": len(deltas), "answer": final,
               "procedures": procedures, "path": path, "session_id": session["session_id"],
               "consent": f"{consent['action']} ({consent['sop_id']} › {consent['step_id']})"
               if consent else "–"}
    if not problems:
        print(f"[result] sop={procedures} path={path} tools={tools} deltas={len(deltas)} "
              + " ".join(f"{k}={v:.1f}s" for k, v in timing.items()))
        problems, extra = decide(session["session_id"], consent["consent_id"], approve)
        metrics.update(extra)
    return problems, metrics


def decide(session_id: str, consent_id: str, approve: bool) -> tuple[list[str], dict]:
    """Human-in-the-loop: send the expert's decision and check what the agent did with it."""
    verdict = "APPROVE" if approve else "DECLINE"
    print(f"\n[expert] {verdict} the reboot")
    body = {"consent_id": consent_id, "approved": approve, "comment": "e2e check"}
    calls, outcomes, final, errors, selected = [], [], "", [], []
    ppp_after = reboot_result = None
    started = time.monotonic()
    for kind, data, at in stream_post(f"/v1/sessions/{session_id}/consent", body):
        step = data.get("step")
        if kind == "tool_call" and data.get("status") == "completed":
            calls.append(data["tool"])
            print(f"  [{at:6.1f}s] tool  {data['tool']} -> {json.dumps(data.get('result'))[:100]}",
                  flush=True)
            if data["tool"] == "reboot_device":
                reboot_result = data.get("result")
            if data["tool"] == "get_ppp_session":
                ppp_after = data.get("result")
        elif kind == "status_update" and step == "sop_outcome":
            outcomes.append(f"{data['step_id']}={data.get('outcome')}")
            print(f"  [{at:6.1f}s] step  {data['step_id']} -> {data.get('outcome')}", flush=True)
        elif kind == "status_update" and step == "sop_selected":
            selected.append(data["doc_id"])
        elif kind == "agent_message" and data.get("final"):
            final = data.get("text", "")
        elif kind == "error":
            errors.append(data.get("message", ""))
    print(f"  answer: {final[:400]}")

    problems = []
    if errors:
        problems.append(f"error events after the decision: {errors}")
    if not final.strip():
        problems.append("no answer after the decision")
    if approve:
        if not isinstance(reboot_result, dict) or reboot_result.get("accepted") is not True:
            problems.append(f"the approved reboot did not run: {reboot_result}")
        if "S3=done" not in outcomes or "S4=up" not in outcomes:
            problems.append(f"expected S3=done and S4=up after the reboot, got {outcomes}")
        if not isinstance(ppp_after, dict) or ppp_after.get("status") != "up":
            problems.append(f"the PPP session should be up after the reboot: {ppp_after}")
    else:
        if "reboot_device" in calls:
            problems.append("the reboot ran although it was declined")
        if "S3=declined" not in outcomes:
            problems.append(f"expected S3=declined, got {outcomes}")
    state = session_state(session_id)
    log = state.get("consent_log", [])
    if len(log) != 1 or log[0]["approved"] is not approve:
        problems.append(f"the decision was not audited correctly: {log}")
    if state.get("pending_consent"):
        problems.append("the approval request is still pending after the decision")
    if not problems:
        print(f"[result] {verdict.lower()}d: outcomes={outcomes} tools={calls} audit={len(log)}")
    return problems, {"decision": verdict.lower(), "decision_path": outcomes,
                      "decision_answer": final, "decision_s": time.monotonic() - started,
                      "decision_tools": calls, "followed": selected}


def run_handover(attempt: int) -> tuple[list[str], dict]:
    """Multi-agent: In-Home agent -> hand-over -> Access-PON agent -> approved booking."""
    customer = "C1001"
    request_json("POST", f"{ORCHESTRATOR}/v1/demo/customers/{customer}/reset")
    session = request_json("POST", f"{ORCHESTRATOR}/v1/sessions",
                           {"customer_id": customer, "expert_name": "e2e"})
    session_id = session["session_id"]
    print(f"\n[handover {attempt}] session {session_id[:8]} · customer {customer}")
    print(f"[expert] {MESSAGE}")

    agents, procedures, tools, errors, consent = [], [], [], [], None
    finals: list[str] = []
    trace_id = None
    for kind, data, at in stream_post(f"/v1/sessions/{session_id}/messages", {"message": MESSAGE}):
        if kind == "agent_handoff":
            agents.append(data.get("to_agent"))
            print(f"\n  [{at:6.1f}s] handoff {data.get('from_agent')} -> {data.get('to_agent')}"
                  f" ({data.get('reason')})", flush=True)
        elif kind == "status_update" and data.get("step") == "sop_selected":
            procedures.append(data["doc_id"])
            print(f"  [{at:6.1f}s] SOP   {data['doc_id']}", flush=True)
        elif kind == "status_update" and data.get("step") == "trace":
            trace_id = data.get("trace_id")
        elif kind == "status_update" and data.get("step") == "handover_unavailable":
            errors.append(f"hand-over not possible: {data.get('reason')}")
        elif kind == "tool_call" and data.get("status") == "started":
            tools.append(data["tool"])
            print(f"  [{at:6.1f}s] tool  {data['tool']}", flush=True)
        elif kind == "consent_request":
            consent = data
            print(f"  [{at:6.1f}s] APPROVAL NEEDED {data.get('action')} {data.get('arguments')}",
                  flush=True)
        elif kind == "agent_message" and data.get("final"):
            finals.append(data.get("text", ""))
        elif kind == "error":
            errors.append(data.get("message", ""))

    problems = [f"error: {e}" for e in errors]
    if agents != ["inhome-agent", "access-pon-agent"]:
        problems.append(f"expected hand-over inhome-agent -> access-pon-agent, got {agents}")
    if procedures != ["SOP-IH-001", "SOP-PON-001"]:
        problems.append(f"expected SOP-IH-001 then SOP-PON-001, followed {procedures}")
    if "search_appointment_slots" not in tools:
        problems.append("the Access-PON agent did not search appointment slots")
    if not consent or consent.get("action") != "book_appointment":
        problems.append(f"expected an approval request for book_appointment, got {consent}")
    metrics = {"agents": agents, "procedures": procedures, "tools": tools,
               "answers": finals, "booked": "–", "trace": {}, "session_id": session_id,
               "customer_id": customer}
    if problems:
        return problems, metrics
    trace_problems, metrics["trace"] = check_trace(trace_id)
    problems += trace_problems

    print("\n[expert] APPROVE the booking")
    body = {"consent_id": consent["consent_id"], "approved": True, "comment": "e2e check"}
    booking = None
    for kind, data, at in stream_post(f"/v1/sessions/{session_id}/consent", body):
        if kind == "tool_call" and data.get("status") == "completed" \
                and data["tool"] == "book_appointment":
            booking = data.get("result")
            print(f"  [{at:6.1f}s] tool  book_appointment -> {json.dumps(booking)[:140]}",
                  flush=True)
        elif kind == "agent_message" and data.get("final"):
            finals.append(data.get("text", ""))
        elif kind == "error":
            problems.append(f"error after the approval: {data.get('message')}")
    if not isinstance(booking, dict) or booking.get("status") != "confirmed":
        problems.append(f"the approved booking was not confirmed: {booking}")
    else:
        metrics["booked"] = f"{booking['booking_id']} {booking['slot']['date']} " \
                            f"{booking['slot']['window']}"
    state = session_state(session_id)
    if len(state.get("consent_log", [])) != 1 or state.get("pending_consent"):
        problems.append("the booking approval was not audited correctly")
    if not problems:
        print(f"[result] agents={agents} sops={procedures} booked={metrics['booked']}")
    return problems, metrics


def check_trace(trace_id: str | None) -> tuple[list[str], dict]:
    """The hand-over turn must be one trace in Jaeger across orchestrator and both agents."""
    if not trace_id:
        return ["the orchestrator did not report a trace id"], {}
    expected = {"orchestrator", "inhome-agent", "access-pon-agent"}
    deadline = time.monotonic() + 60
    spans, services = [], set()
    while time.monotonic() < deadline:
        try:
            data = request_json("GET", f"{JAEGER}/api/traces/{trace_id}")["data"][0]
        except (urllib.error.URLError, KeyError, IndexError, ConnectionError):
            data = None
        if data:
            names = {p: v["serviceName"] for p, v in data["processes"].items()}
            spans = data["spans"]
            services = {names[s["processID"]] for s in spans}
            if expected <= services:
                break
        time.sleep(2)
    operations = [s["operationName"] for s in spans]
    summary = {"trace_id": trace_id, "spans": len(spans), "services": sorted(services),
               "sop_steps": sum(o.startswith("sop.step") for o in operations),
               "llm_calls": operations.count("llm.chat"),
               "mcp_calls": operations.count("mcp.call_tool")}
    problems = []
    if not expected <= services:
        problems.append(f"trace {trace_id} covers {sorted(services)}, expected {sorted(expected)}")
    for what, key in (("SOP step", "sop_steps"), ("LLM call", "llm_calls"),
                      ("MCP tool call", "mcp_calls")):
        if not summary[key]:
            problems.append(f"the trace has no {what} spans")
    if not problems:
        print(f"[trace] {trace_id} · {summary['spans']} spans · services {summary['services']} · "
              f"{summary['sop_steps']} SOP steps · {summary['llm_calls']} LLM calls · "
              f"{summary['mcp_calls']} MCP calls")
    return problems, summary


def trace_line(trace: dict) -> str:
    if not trace.get("spans"):
        return ""
    return (f"**Trace in Jaeger** `{trace['trace_id']}`: {trace['spans']} spans across "
            f"{', '.join(trace['services'])} · {trace['sop_steps']} SOP steps · "
            f"{trace['llm_calls']} LLM calls · {trace['mcp_calls']} MCP tool calls\n\n")


# (file, a question the document answers, text the passage must contain, rank it must reach).
# The table question may rank behind the general indicator-light article, so top 3 is enough.
DOCUMENT_CHECKS = [
    ("FibreLink-ONT-100-quick-guide.docx",
     "How long do I hold the reset button of the FibreLink ONT-100?", "12 seconds", 1),
    ("FibreLink-ONT-200-installation-sheet.pdf",
     "Which power adapter does the FibreLink ONT-200 use?", "12 V DC", 1),
    ("FibreLink-ONT-200-installation-sheet.pdf",
     "ONT-200 blinking green PON light ranging searching for the OLT", "Ranging", 3),
]
DOCUMENT_QUESTION = "How long do I hold the reset button to factory reset the FibreLink ONT-100?"


def upload_document(path: Path) -> dict:
    """POST the file to the knowledge service (multipart, stdlib only)."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
            ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{KNOWLEDGE}/v1/documents/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=900) as response:  # the first PDF is slow on a CPU
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"[fail] upload of {path.name} returned HTTP {exc.code}: {exc.read()[:300]!r}")


def check_documents() -> tuple[list[str], list[dict]]:
    """Docling converts real files; the result must be searchable like any other knowledge."""
    wait_for("docling", f"{DOCLING}/health")
    problems, rows = [], []
    uploads: dict[str, dict] = {}
    for filename, question, expected_text, rank in DOCUMENT_CHECKS:
        if filename not in uploads:
            uploads[filename] = upload_document(DOCUMENTS / filename)
            added = uploads[filename]
            print(f"  [upload] {filename} -> {added['doc_id']} ({added['sections']} sections, "
                  f"{added['chunks']} chunks, converted by {added['converted_by']} in "
                  f"{added['convert_s']} s)")
            if added["converted_by"] != "docling" or added["sections"] < 2:
                problems.append(f"{filename} was not converted by Docling into sections: {added}")
        added = uploads[filename]
        hits = search_knowledge(question, "access-pon")[:rank]
        found = next((h for h in hits if h["doc_id"] == added["doc_id"]
                      and expected_text in h["text"]), None)
        shown = found or (hits[0] if hits else {})
        rows.append({**added, "file": filename, "question": question, "ok": found is not None,
                     "citation": shown.get("citation")})
        print(f"  [{'ok' if found else 'FAIL'}] {question!r} -> {shown.get('citation')}")
        if not found:
            problems.append(f"{question!r}: no hit in the top {rank} is {added['doc_id']} "
                            f"containing {expected_text!r} (top: {shown.get('doc_id')})")
    return problems, rows


def ask_about_uploaded_document(doc_id: str) -> tuple[list[str], str]:
    """The expert asks a question only the uploaded Word guide answers; the agent must cite it."""
    session = request_json("POST", f"{ORCHESTRATOR}/v1/sessions",
                           {"customer_id": "C1006", "expert_name": "e2e"})
    print(f"\n[expert] {DOCUMENT_QUESTION}")
    cited, answer, errors = [], "", []
    path = f"/v1/sessions/{session['session_id']}/messages"
    for kind, data, _at in stream_post(path, {"message": DOCUMENT_QUESTION}):
        if kind == "knowledge_article":
            cited.append(data.get("doc_id"))
        elif kind == "agent_message" and data.get("final"):
            answer = data.get("text", "")
        elif kind == "error":
            errors.append(data.get("message", ""))
    print(f"  cited {cited}\n  answer: {answer[:300]}")
    problems = [f"error: {e}" for e in errors]
    if doc_id not in cited:
        problems.append(f"the answer did not cite the uploaded document {doc_id}: {cited}")
    if not answer.strip():
        problems.append("no answer to the question about the uploaded document")
    return problems, answer


def write_documents_summary(rows: list[dict], answer: str, passed: bool) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"### Document processing (Docling) {'✅' if passed else '❌'}\n",
             "| File | Stored as | Sections | Chunks | Converted in | Question → best passage |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['file']} | {row['doc_id']} v{row['version']} | {row['sections']} | "
                     f"{row['chunks']} | {row['convert_s']} s | {row['question']} → "
                     f"{row['citation']} {'✅' if row['ok'] else '❌'} |")
    lines.append(f"\n> Question in the chat: *{DOCUMENT_QUESTION}*  \n"
                 f"> {answer.replace(chr(10), ' ')[:500]}\n")
    with open(path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n\n")


def check_memory_after_restart(session_id: str, customer_id: str) -> list[str]:
    """Sessions live in Postgres: a restarted orchestrator must still know the whole case."""
    before = session_state(session_id)
    print("\n[memory] restarting the orchestrator container ...")
    subprocess.run(["docker", "compose", "restart", "orchestrator"], check=True)
    wait_for("orchestrator", f"{ORCHESTRATOR}/health")
    after = session_state(session_id)
    problems = []
    for key in ("history", "consent_log", "active_agent", "agent_state", "created_at"):
        if after.get(key) != before.get(key):
            problems.append(f"{key} changed across the restart")
    if len(after.get("history", [])) < 2:
        problems.append("the conversation history is missing after the restart")
    earlier = request_json("GET", f"{ORCHESTRATOR}/v1/sessions?customer_id={customer_id}")
    if session_id not in [case["session_id"] for case in earlier]:
        problems.append("the case is not listed among the customer's earlier cases")
    if not problems:
        print(f"[memory] ok: {len(after['history'])} messages, {len(after['consent_log'])} "
              f"decision(s), active agent {after['active_agent']}; {len(earlier)} earlier case(s)")
    return problems


def write_handover_summary(metrics: dict, passed: bool) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    answers = " / ".join(a.replace("\n", " ")[:300] for a in metrics.get("answers", []))
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(
            f"### Multi-agent hand-over {'✅' if passed else '❌'}\n\n"
            f"Customer `C1001` · agents: {' → '.join(['inhome-agent', *metrics['agents']])}"
            f" · SOPs: {' → '.join(metrics['procedures']) or '–'}\n\n"
            f"Tools: {', '.join(metrics['tools']) or '–'} · **booking after approval:** "
            f"{metrics['booked']}\n\n> {answers}\n\n"
            + trace_line(metrics.get("trace") or {})
        )


def session_state(session_id: str) -> dict:
    return request_json("GET", f"{ORCHESTRATOR}/v1/sessions/{session_id}")


def write_summary(metrics: dict, passed: bool) -> None:
    """Add a latency table to the GitHub Actions run page (no-op elsewhere)."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return

    def fmt(key: str) -> str:
        return f"{metrics[key]:.1f} s" if key in metrics else "–"

    answer = metrics.get("answer", "").replace("\n", " ")
    model = os.getenv("OLLAMA_CHAT_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(
            f"### End-to-end conversation {'✅' if passed else '❌'}\n\n"
            f"Customer `{CUSTOMER}` · model `{model}` on a CPU-only runner\n\n"
            "| First event | First tool call | First answer token | Whole turn | Tools | Tokens |\n"
            "|---|---|---|---|---|---|\n"
            f"| {fmt('first_event')} | {fmt('first_tool')} | {fmt('first_token')} | "
            f"{fmt('total')} | {', '.join(metrics.get('tools', [])) or '–'} | "
            f"{metrics.get('deltas', 0)} |\n\n"
            f"**SOP:** {', '.join(metrics.get('procedures', [])) or '–'} · "
            f"**path:** {' → '.join(metrics.get('path', [])) or '–'} · "
            f"**approval requested:** {metrics.get('consent', '–')}\n\n"
            f"**Expert's decision: {metrics.get('decision', '–').upper()}** → "
            f"{' → '.join(metrics.get('decision_path', [])) or '–'} · tools after the "
            f"decision: {', '.join(metrics.get('decision_tools', [])) or 'none'}\n\n"
            f"> {answer[:600]}\n\n"
            f"> After the decision: "
            f"{metrics.get('decision_answer', '').replace(chr(10), ' ')[:500]}\n\n"
        )


def search_knowledge(question: str, agent: str) -> list[dict]:
    query = urlencode({"q": question, "agent": agent, "top_k": 3})
    return request_json("GET", f"{KNOWLEDGE}/v1/search?{query}")


def check_knowledge() -> tuple[list[str], list[dict]]:
    """Real embeddings + pgvector: each question must surface an acceptable document first."""
    documents = request_json("GET", f"{KNOWLEDGE}/v1/documents")
    print(f"\n[knowledge] {len(documents)} documents ingested")
    problems, rows = [], []
    if len(documents) < 10:
        problems.append(f"only {len(documents)} knowledge documents ingested")
    for question, agent, expected in KNOWLEDGE_CHECKS:
        hits = search_knowledge(question, agent)
        top = hits[0] if hits else {}
        ok = top.get("doc_id") in expected
        rows.append({"question": question, "agent": agent, "ok": ok, **{
            k: top.get(k) for k in ("citation", "similarity", "keyword_match")}})
        print(f"  [{'ok' if ok else 'FAIL'}] {question!r} -> {top.get('citation')} "
              f"(similarity {top.get('similarity')}, keyword {top.get('keyword_match')})")
        if not ok:
            problems.append(f"{question!r} found {top.get('doc_id')}, expected one of {expected}")
    off_hits = search_knowledge(*OFF_TOPIC)
    rows.append({"question": OFF_TOPIC[0], "agent": OFF_TOPIC[1], "ok": None,
                 "citation": off_hits[0]["citation"] if off_hits else "(nothing)",
                 "similarity": off_hits[0]["similarity"] if off_hits else None,
                 "keyword_match": off_hits[0]["keyword_match"] if off_hits else None})
    print(f"  [info] off-topic {OFF_TOPIC[0]!r} -> {len(off_hits)} hit(s)"
          + (f", best similarity {off_hits[0]['similarity']}" if off_hits else ""))
    return problems, rows


def write_knowledge_summary(rows: list[dict]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["### Knowledge search (bge-m3 + pgvector hybrid)\n",
             "| Question | Agent | Top result | Similarity | Keyword | OK |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        mark = {True: "✅", False: "❌", None: "info"}[row["ok"]]
        lines.append(f"| {row['question']} | {row['agent']} | {row['citation']} | "
                     f"{row['similarity']} | {row['keyword_match']} | {mark} |")
    with open(path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n\n")


def main() -> None:
    wait_for("orchestrator", f"{ORCHESTRATOR}/health")
    wait_for("chat-ui", f"{CHAT_UI}/_stcore/health")
    wait_for("knowledge-mcp", f"{KNOWLEDGE}/health")
    wait_for("jaeger", f"{JAEGER}/")
    customers = [c["customer_id"] for c in request_json("GET", f"{ORCHESTRATOR}/v1/demo/customers")]
    print(f"[ready] scenario customers: {', '.join(customers)}")

    knowledge_problems, rows = check_knowledge()
    write_knowledge_summary(rows)
    if knowledge_problems:
        sys.exit(f"\nE2E FAILED (knowledge): {'; '.join(knowledge_problems)}")

    print("\n[documents] Docling conversion and search")
    document_problems, document_rows = check_documents()
    if document_problems:
        write_documents_summary(document_rows, "", passed=False)
        sys.exit(f"\nE2E FAILED (documents): {'; '.join(document_problems)}")
    uploaded = document_rows[0]["doc_id"]
    answer, chat_problems = "", []
    for attempt in range(1, ATTEMPTS + 1):
        chat_problems, answer = ask_about_uploaded_document(uploaded)
        if not chat_problems:
            break
        print(f"[attempt {attempt}] problems: {'; '.join(chat_problems)}")
    write_documents_summary(document_rows, answer, passed=not chat_problems)
    if chat_problems:
        sys.exit(f"\nE2E FAILED (document question): {'; '.join(chat_problems)}")

    # Small local models are not fully deterministic, so each scenario gets one retry.
    results: list[dict] = []
    # The decline path is covered by the agent and orchestrator tests; here every minute counts.
    scenarios = [
        ("approve", run_attempt, write_summary),
        ("hand-over", run_handover, write_handover_summary),
    ]
    for name, run, summarize in scenarios:
        metrics: dict = {}
        for attempt in range(1, ATTEMPTS + 1):
            problems, metrics = run(attempt)
            if not problems:
                break
            print(f"[attempt {attempt}] problems: {'; '.join(problems)}")
        else:
            summarize(metrics, passed=False)
            sys.exit(f"\nE2E FAILED ({name} scenario)")
        summarize(metrics, passed=True)
        results.append(metrics)
    if os.getenv("E2E_RESTART") == "1":
        handover = results[-1]
        problems = check_memory_after_restart(handover["session_id"], handover["customer_id"])
        if problems:
            sys.exit(f"\nE2E FAILED (memory): {'; '.join(problems)}")
    print("\nE2E PASSED - knowledge search, Docling document processing, SOP-guided "
          "troubleshooting, approval, the multi-agent hand-over and memory work end to end.")


if __name__ == "__main__":
    main()
