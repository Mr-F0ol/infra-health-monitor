"""OpenTelemetry tracing — opt-in distributed traces for the API.

Kept out of the core dependency set: the heavy OTel packages live in the `otel`
extra and are imported lazily here, so a default install stays lean and nothing
changes unless ``MONITOR_OTEL_ENABLED=true`` *and* the extra is installed.

When enabled, FastAPI requests are auto-instrumented and spans are exported via
OTLP/gRPC to ``MONITOR_OTEL_EXPORTER_OTLP_ENDPOINT`` (a collector, Tempo, Jaeger,
etc.). Trace context propagates through inbound/outbound HTTP automatically.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import settings

logger = logging.getLogger(__name__)


def configure_tracing(app: FastAPI) -> None:
    """Instrument ``app`` with OpenTelemetry when enabled; otherwise a no-op."""
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "MONITOR_OTEL_ENABLED is set but OpenTelemetry is not installed; "
            "run `pip install '.[otel]'`. Tracing disabled."
        )
        return

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    logger.info(
        "OpenTelemetry tracing enabled — exporting to %s",
        settings.otel_exporter_otlp_endpoint,
    )
