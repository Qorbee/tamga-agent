"""Traces of agent runs, sent to Langfuse Cloud over OpenTelemetry (ADR-0007).

With the Langfuse keys unset nothing is exported, and agent runs carry no instrumentation at all.
What a span holds is decided per run (``llm.run``): prompts, answers and tool arguments only for a
company whose data is synthetic; for a pilot company, timing, tokens, model and tool names only.

Spans are batched and sent from the exporter's own thread, so a slow or unreachable Langfuse never
holds up a request or a job; what cannot be sent is dropped.
"""

import base64

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings

_provider: TracerProvider | None = None


def init_tracing(service: str) -> None:
    """Starts exporting, when the Langfuse keys are set. ``service`` names the process (api,
    worker) on every span."""
    global _provider
    settings = get_settings()
    public, secret = settings.langfuse_public_key, settings.langfuse_secret_key
    if public is None or secret is None:
        return
    credentials = f"{public}:{secret.get_secret_value()}"
    exporter = OTLPSpanExporter(
        endpoint=settings.langfuse_base_url.rstrip("/") + "/api/public/otel/v1/traces",
        headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()},
    )
    provider = TracerProvider(resource=Resource.create({"service.name": f"tamga-{service}"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    _provider = provider


def shutdown_tracing() -> None:
    """Sends what is still batched, then stops."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def tracer_provider() -> TracerProvider | None:
    return _provider
