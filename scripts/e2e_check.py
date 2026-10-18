#!/usr/bin/env python3
"""End-to-end check of the running product (docker compose up -d), using only the stdlib.

Waits for the services, resets scenario customer C1002 (stuck modem), opens a session and runs
one troubleshooting turn through orchestrator -> In-Home agent -> MCP -> simulator with the real
local LLM. Passes when the answer streams token by token, at least one diagnostic tool ran and
no error occurred.

    python scripts/e2e_check.py            # ORCHESTRATOR_URL / CHAT_UI_URL override the defaults
"""

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

ORCHESTRATOR = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000").rstrip("/")
CHAT_UI = os.getenv("CHAT_UI_URL", "http://localhost:8501").rstrip("/")
WAIT_S = int(os.getenv("E2E_WAIT_S", "900"))
CUSTOMER = os.getenv("E2E_CUSTOMER", "C1002")
MESSAGE = "The customer reports they have no internet. Please diagnose the connection."
ATTEMPTS = 2


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


def stream_turn(session_id: str):
    """POST a message and yield (event_type, payload, seconds_since_start) as frames arrive."""
    target = urlparse(ORCHESTRATOR)
    conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=900)
    started = time.monotonic()
    conn.request("POST", f"/v1/sessions/{session_id}/messages",
                 body=json.dumps({"message": MESSAGE}),
                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    response = conn.getresponse()
    if response.status != 200:
        sys.exit(f"[fail] turn returned HTTP {response.status}: {response.read()[:300]!r}")
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


def run_attempt(attempt: int) -> tuple[list[str], dict]:
    request_json("POST", f"{ORCHESTRATOR}/v1/demo/customers/{CUSTOMER}/reset")
    session = request_json("POST", f"{ORCHESTRATOR}/v1/sessions",
                           {"customer_id": CUSTOMER, "expert_name": "e2e"})
    print(f"\n[turn {attempt}] session {session['session_id'][:8]} · customer {CUSTOMER}")
    print(f"[expert] {MESSAGE}")

    tools, deltas, final, errors = [], [], "", []
    timing: dict[str, float] = {}
    for kind, data, at in stream_turn(session["session_id"]):
        timing.setdefault("first_event", at)
        if kind == "tool_call" and data.get("status") == "started":
            timing.setdefault("first_tool", at)
            tools.append(data["tool"])
            print(f"\n  [{at:6.1f}s] tool  {data['tool']}", flush=True)
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
    metrics = {**timing, "tools": tools, "deltas": len(deltas), "answer": final}
    if not problems:
        print(f"[result] tools={tools} deltas={len(deltas)} "
              + " ".join(f"{k}={v:.1f}s" for k, v in timing.items()))
    return problems, metrics


def write_summary(metrics: dict, passed: bool) -> None:
    """Add a latency table to the GitHub Actions run page (no-op elsewhere)."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return

    def fmt(key: str) -> str:
        return f"{metrics[key]:.1f} s" if key in metrics else "–"

    answer = metrics.get("answer", "").replace("\n", " ")
    with open(path, "a", encoding="utf-8") as summary:
        summary.write(
            f"### End-to-end conversation {'✅' if passed else '❌'}\n\n"
            f"Customer `{CUSTOMER}` · model `{os.getenv('OLLAMA_CHAT_MODEL', 'qwen3:4b-instruct-2507-q4_K_M')}` "
            f"on a CPU-only runner\n\n"
            "| First event | First tool call | First answer token | Whole turn | Tools | Tokens |\n"
            "|---|---|---|---|---|---|\n"
            f"| {fmt('first_event')} | {fmt('first_tool')} | {fmt('first_token')} | "
            f"{fmt('total')} | {', '.join(metrics.get('tools', [])) or '–'} | "
            f"{metrics.get('deltas', 0)} |\n\n"
            f"> {answer[:600]}\n\n"
        )


def main() -> None:
    wait_for("orchestrator", f"{ORCHESTRATOR}/health")
    wait_for("chat-ui", f"{CHAT_UI}/_stcore/health")
    customers = [c["customer_id"] for c in request_json("GET", f"{ORCHESTRATOR}/v1/demo/customers")]
    print(f"[ready] scenario customers: {', '.join(customers)}")

    # Small local models are not fully deterministic, so allow one retry.
    metrics: dict = {}
    for attempt in range(1, ATTEMPTS + 1):
        problems, metrics = run_attempt(attempt)
        if not problems:
            write_summary(metrics, passed=True)
            print("\nE2E PASSED - real-time troubleshooting conversation works end to end.")
            return
        print(f"[attempt {attempt}] problems: {'; '.join(problems)}")
    write_summary(metrics, passed=False)
    sys.exit("\nE2E FAILED")


if __name__ == "__main__":
    main()
