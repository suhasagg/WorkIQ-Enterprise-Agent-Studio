"""OpenTelemetry tracing. Spans are always created; they are exported only when an OTLP endpoint is configured."""
from __future__ import annotations

from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from app.config import settings

_configured = False
SPANS_CREATED = 0


def configure_telemetry() -> dict:
    global _configured
    if not _configured:
        provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
        if settings.otel_exporter_otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            provider.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)
        _configured = True
    return status()


def status() -> dict:
    return {
        "opentelemetry": {"enabled": True, "exporting": bool(settings.otel_exporter_otlp_endpoint),
                          "endpoint_configured": bool(settings.otel_exporter_otlp_endpoint), "spans_created": SPANS_CREATED},
        "langsmith": {"configured": bool(settings.langsmith_api_key),
                      "tracing": bool(settings.langsmith_api_key) and settings.langsmith_tracing.lower() == "true"},
    }


tracer = trace.get_tracer("workiq.agent-studio")


@contextmanager
def span(name: str, **attrs):
    global SPANS_CREATED
    SPANS_CREATED += 1
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        yield s
