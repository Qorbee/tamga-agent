"""Traces of agent runs, sent to Langfuse Cloud over OpenTelemetry (ADR-0007).

With the Langfuse keys unset nothing is exported, and agent runs carry no instrumentation at all.
What a span holds is decided per run (``llm.run``): prompts, answers and tool arguments only for a
company whose data is synthetic; for a pilot company, timing, tokens, model and tool names only.

Spans are batched and sent from the exporter's own thread, so a slow or unreachable Langfuse never
holds up a request or a job; what cannot be sent is dropped.

Langfuse keeps spans, not traces: what a span does not carry itself cannot be filtered or summed
by. So each span of a run is named after its agent (``AgentName``), and cost is read per agent
from the model calls themselves.
"""

import base64
from typing import Final

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings

# What the model layer puts on every span of a run, and where Langfuse reads a trace's name.
_AGENT: Final = "gen_ai.agent.name"
_TRACE_NAME: Final = "langfuse.trace.name"

_provider: TracerProvider | None = None


class AgentName(SpanProcessor):
    """Names the trace of every span of a run after the run's agent, on the span itself."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        agent = (span.attributes or {}).get(_AGENT)
        if agent is not None:
            span.set_attribute(_TRACE_NAME, agent)


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
        headers={
            "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode(),
            # Langfuse's current ingestion: spans are stored as they arrive. Without it they
            # take the older path, which is delayed and ends on 2026-11-16.
            "x-langfuse-ingestion-version": "4",
        },
    )
    provider = TracerProvider(resource=Resource.create({"service.name": f"tamga-{service}"}))
    provider.add_span_processor(AgentName())
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
