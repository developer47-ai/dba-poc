"""Distributed tracing with OpenTelemetry: one trace per chat turn, across all services.

Tracing is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set (compose points it at Jaeger). When it
is on, FastAPI requests and outgoing HTTP calls are traced automatically and the W3C
`traceparent` header carries the trace from the orchestrator to the agents and MCP servers. The
code adds a few meaningful spans on top (`span`, `annotate`). Without a provider every helper is
a cheap no-op, so the rest of the code never needs to check.
"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from poc_common.config import Config, get_config

logger = logging.getLogger(__name__)

TRACER_NAME = "dba-poc"
EXPORT_DELAY_MS = 1000  # short, so a finished chat turn is visible in Jaeger within seconds

_provider: TracerProvider | None = None


def tracing_enabled() -> bool:
    return _provider is not None


def setup_tracing(
    service_name: str, config: Config | None = None, *, exporter: SpanExporter | None = None
) -> bool:
    """Turn tracing on for this process; returns whether it is on.

    `exporter` is for tests (an in-memory exporter); otherwise spans go to the OTLP endpoint.
    """
    global _provider
    config = config or get_config()
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter)
    elif config.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = f"{config.otlp_endpoint.rstrip('/')}/v1/traces"
        processor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint), schedule_delay_millis=EXPORT_DELAY_MS
        )
    else:
        return False

    shutdown_tracing()
    _provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "service.namespace": "dba-poc"})
    )
    _provider.add_span_processor(processor)

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument(tracer_provider=_provider)
    logger.info("tracing enabled for %s", service_name)
    return True


def shutdown_tracing() -> None:
    """Flush and switch tracing off (used on exit and between tests)."""
    global _provider
    if _provider is None:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().uninstrument()
    _provider.shutdown()
    _provider = None


def instrument_fastapi(app: Any) -> None:
    """Trace every request of a FastAPI app (health checks excluded); no-op when tracing is off."""
    if _provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=_provider, excluded_urls="health",
        exclude_spans=["send", "receive"],  # internal ASGI spans only add noise
    )


def get_tracer() -> trace.Tracer:
    return _provider.get_tracer(TRACER_NAME) if _provider else trace.get_tracer(TRACER_NAME)


@contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """A child span of the current one; an exception marks it as failed and propagates."""
    with get_tracer().start_as_current_span(name, attributes=_clean(attributes)) as current:
        yield current


def annotate(attributes: Mapping[str, Any] | None = None, *, name: str | None = None) -> None:
    """Add attributes (and optionally a better name) to the current span."""
    current = trace.get_current_span()
    if not current.is_recording():
        return
    if name:
        current.update_name(name)
    for key, value in _clean(attributes).items():
        current.set_attribute(key, value)


def fail(current: Span, error: BaseException | str) -> None:
    current.set_status(Status(StatusCode.ERROR, str(error)[:200]))


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def _clean(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """OpenTelemetry attributes must be simple values; drop None and stringify the rest."""
    clean: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        clean[key] = value if isinstance(value, str | bool | int | float) else str(value)
    return clean
