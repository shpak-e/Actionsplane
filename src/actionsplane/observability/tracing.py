"""OpenTelemetry tracing — one trace from webhook ingest through the worker (plan §obs).

Design constraints:

* **Optional.** Off unless ``ACTIONSPLANE_OTEL_ENABLED=true``; then spans export over OTLP.
* **Import-safe.** If the OTel SDK isn't installed, every hook is a no-op — the app still imports
  and runs. This matters for the hermetic test env and minimal images.
* **Cross-process.** arq doesn't propagate context, so the ingest side injects the W3C
  ``traceparent`` into the enqueued job and the worker extracts it — that's what stitches
  ingest → process_event → audit_repo → SARIF into a single distributed trace rather than four
  disconnected ones.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from actionsplane.config import get_settings

log = logging.getLogger(__name__)

try:
    from opentelemetry import propagate, trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL_AVAILABLE = True
except Exception:  # SDK not installed — run without tracing
    _OTEL_AVAILABLE = False

_TRACER_NAME = "actionsplane"
_configured = False


def _active() -> bool:
    """True only when the SDK is importable AND the operator opted in."""
    return _OTEL_AVAILABLE and get_settings().otel_enabled


def setup_tracing(service_name: str) -> None:
    """Configure the global tracer provider + OTLP exporter exactly once. No-op if disabled."""
    global _configured
    if _configured or not _active():
        return
    settings = get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = (
            OTLPSpanExporter(endpoint=settings.otel_endpoint)
            if settings.otel_endpoint
            else OTLPSpanExporter()  # falls back to OTEL_EXPORTER_OTLP_ENDPOINT / localhost:4317
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:  # exporter unavailable — keep tracing in-process rather than crash
        log.warning("OTLP span exporter unavailable; spans will not be exported", exc_info=True)
    trace.set_tracer_provider(provider)
    _configured = True
    log.info("tracing enabled for service %s", service_name)


def instrument_fastapi(app) -> None:
    """Auto-instrument a FastAPI/ASGI app's request spans. No-op if disabled/unavailable."""
    if not _active():
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        log.warning("FastAPI instrumentation unavailable", exc_info=True)


def inject_context() -> dict[str, str]:
    """Serialize the current trace context into a carrier dict (W3C ``traceparent``).

    The ingestor stashes this on the enqueued job so the worker can continue the same trace.
    Returns an empty dict when tracing is off — harmless to enqueue.
    """
    if not _active():
        return {}
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def continue_trace(carrier: dict | None, name: str, **attrs):
    """Start a span as a child of the context carried in ``carrier`` (the worker side)."""
    if not _active():
        yield None
        return
    ctx = propagate.extract(carrier or {})
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name, context=ctx) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s


@contextmanager
def span(name: str, **attrs):
    """Start an in-process child span under whatever span is currently active."""
    if not _active():
        yield None
        return
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s
