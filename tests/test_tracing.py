"""Tracing helpers: no-op when disabled, and real cross-carrier context propagation when on.

The propagation test is the important one — it proves the ingest span and the worker span land in
the *same* trace (worker parented on ingest) when the W3C carrier is passed across the queue.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from actionsplane.observability import tracing


def test_helpers_are_noops_when_disabled(monkeypatch):
    monkeypatch.setattr(tracing, "_active", lambda: False)
    assert tracing.inject_context() == {}
    with tracing.span("audit.audit_repo", repo="x/y") as s:
        assert s is None
    with tracing.continue_trace({"traceparent": "bogus"}, "worker.process_event") as s:
        assert s is None  # never raises, even on a junk carrier


def test_context_propagates_across_carrier(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    monkeypatch.setattr(tracing, "_active", lambda: True)
    monkeypatch.setattr(tracing.trace, "get_tracer", lambda *a, **k: tracer)

    # ingest side: open a span, serialize its context into a carrier (what enqueue_event does)
    with tracer.start_as_current_span("ingest"):
        carrier = tracing.inject_context()
    assert "traceparent" in carrier

    # worker side: continue the trace from the carrier (what process_event does)
    with tracing.continue_trace(carrier, "worker.process_event", event="workflow_run"):
        pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert {"ingest", "worker.process_event"} <= set(spans)
    ingest, worker = spans["ingest"], spans["worker.process_event"]
    assert worker.context.trace_id == ingest.context.trace_id  # one trace, not two
    assert worker.parent is not None
    assert worker.parent.span_id == ingest.context.span_id  # worker is a child of ingest
    assert worker.attributes.get("event") == "workflow_run"
