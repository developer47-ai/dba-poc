"""Test helper: run an ASGI app on a real local port in a background thread."""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def run_app_in_thread(app, *, port: int | None = None) -> Iterator[str]:
    """Serve `app` and yield its base URL (e.g. http://127.0.0.1:5123); stop on exit."""
    port = port or free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def capture_spans() -> Iterator[InMemorySpanExporter]:
    """Turn tracing on for a test and collect the finished spans in memory."""
    from poc_common.tracing import setup_tracing, shutdown_tracing

    exporter = InMemorySpanExporter()
    setup_tracing("test-service", exporter=exporter)
    try:
        yield exporter
    finally:
        shutdown_tracing()