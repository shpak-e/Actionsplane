"""Observability — OpenTelemetry tracing wiring (optional, import-safe).

Public surface used across the app:

* ``setup_tracing(service_name)`` — configure the global tracer + OTLP exporter (once).
* ``instrument_fastapi(app)`` — auto-instrument an ASGI app's HTTP spans.
* ``span(name, **attrs)`` — context manager for an in-process child span.
* ``inject_context()`` / ``continue_trace(carrier, name, **attrs)`` — carry the trace across the
  arq queue so the worker's processing span is a child of the ingest span (one end-to-end trace).

Everything degrades to a no-op when ``otel_enabled`` is false or the SDK isn't importable, so the
control plane behaves identically with observability off.
"""

from actionsplane.observability.tracing import (
    continue_trace,
    inject_context,
    instrument_fastapi,
    setup_tracing,
    span,
)

__all__ = [
    "continue_trace",
    "inject_context",
    "instrument_fastapi",
    "setup_tracing",
    "span",
]
