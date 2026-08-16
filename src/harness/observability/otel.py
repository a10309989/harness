"""Optional OpenTelemetry wiring for API and downstream components."""

from __future__ import annotations

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def configure_otel(app, settings) -> bool:
    """Configure OTLP tracing when dependencies and config are present."""
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OpenTelemetry dependencies are not installed; tracing disabled")
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        )
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    app.state.otel_tracing_enabled = True
    return True


@contextmanager
def traced_span(name: str, **attributes):
    try:
        from opentelemetry import trace
    except ImportError:
        yield
        return
    tracer = trace.get_tracer("harness")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield span
