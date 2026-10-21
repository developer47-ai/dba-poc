#!/usr/bin/env python3
"""End-to-end check of the running product (docker compose up -d), using only the stdlib.

1. Knowledge base: realistic questions must find the right SOP through knowledge-mcp (real bge-m3
   embeddings + pgvector hybrid search).
2. Conversation: resets scenario customer C1002 (stuck modem), opens a session and runs one
   troubleshooting turn through orchestrator -> In-Home agent -> MCP -> simulator with the real
   local LLM. Passes when the answer streams token by token, at least one diagnostic tool ran and
   no error occurred.

    python scripts/e2e_check.py   # ORCHESTRATOR_URL / CHAT_UI_URL / KNOWLEDGE_URL override defaults
"""

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

ORCHESTRATOR = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000").rstrip("/")
CHAT_UI = os.getenv("CHAT_UI_URL", "http://localhost:8501").rstrip("/")
KNOWLEDGE = os.getenv("KNOWLEDGE_URL", "http://localhost:8105").rstrip("/")
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
    customers = [c["customer_id"] for c in request_json("GET", f"{ORCHESTRATOR}/v1/demo/customers")]
    print(f"[ready] scenario customers: {', '.join(customers)}")

    knowledge_problems, rows = check_knowledge()
    write_knowledge_summary(rows)
    if knowledge_problems:
        sys.exit(f"\nE2E FAILED (knowledge): {'; '.join(knowledge_problems)}")

    # Small local models are not fully deterministic, so each scenario gets one retry.
    results: list[dict] = []
    for approve in (True, False):
        metrics: dict = {}
        for attempt in range(1, ATTEMPTS + 1):
            problems, metrics = run_attempt(attempt, approve)
            if not problems:
                break
            print(f"[attempt {attempt}] problems: {'; '.join(problems)}")
        else:
            write_summary(metrics, passed=False)
            sys.exit(f"\nE2E FAILED ({'approve' if approve else 'decline'} scenario)")
        write_summary(metrics, passed=True)
        results.append(metrics)
    print("\nE2E PASSED - knowledge search, SOP-guided troubleshooting and the approve / decline "
          "flow work end to end.")


if __name__ == "__main__":
    main()
