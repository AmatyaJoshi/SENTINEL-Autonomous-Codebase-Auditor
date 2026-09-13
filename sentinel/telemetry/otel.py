"""OpenTelemetry setup (SPEC.md §9). OTLP when configured, console fallback, no-op otherwise."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, Tracer

from sentinel import __version__
from sentinel.config import Settings

_initialised = False


def init_telemetry(settings: Settings) -> Tracer:
    """Idempotent. Uses Traceloop (OpenLLMetry) when an OTLP endpoint is set."""
    global _initialised
    if _initialised:
        return trace.get_tracer("sentinel")

    resource = Resource.create({"service.name": "sentinel", "service.version": __version__})
    provider = TracerProvider(resource=resource)

    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        try:
            from traceloop.sdk import Traceloop

            Traceloop.init(app_name="sentinel", exporter=exporter, disable_batch=False)
        except Exception:  # noqa: BLE001 - telemetry must never break an audit
            pass
    elif settings.telemetry_console:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _initialised = True
    return trace.get_tracer("sentinel")


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    """Child span carrying `sentinel.*` attributes. Safe before init (no-op tracer)."""
    tracer = trace.get_tracer("sentinel")
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(f"sentinel.{k}", v)
        yield s
