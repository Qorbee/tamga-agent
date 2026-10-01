import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.test import TestModel

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.platform import llm, tracing

APP = Path(__file__).resolve().parents[2]
PROMPT = "Plate 34 ABC 12, IBAN TR33 0006 1005 1978 6457 8413 26"
ANSWER = "the answer a pilot's mail produced"


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_provider", provider)
    yield exporter
    provider.shutdown()


def traced_text(exporter: InMemorySpanExporter) -> str:
    return " ".join(
        f"{span.name} {key} {value}"
        for span in exporter.get_finished_spans()
        for key, value in (span.attributes or {}).items()
    )


async def run_probe(trace_content: bool) -> None:
    agent: Agent[None, str] = Agent(name="probe")
    with agent.override(model=TestModel(custom_output_text=ANSWER)):
        result = await llm.run(
            agent,
            PROMPT,
            deps=None,
            limits=UsageLimits(request_limit=2),
            trace_content=trace_content,
        )
    assert result.output == ANSWER


async def test_a_pilot_run_is_traced_without_its_content(spans: InMemorySpanExporter) -> None:
    await run_probe(trace_content=companies.traces_content(CompanyKind.PILOT))

    traced = traced_text(spans)
    assert spans.get_finished_spans()  # timing, tokens and the model are still traced
    assert "34 ABC 12" not in traced and ANSWER not in traced


async def test_a_sandbox_run_is_traced_with_its_content(spans: InMemorySpanExporter) -> None:
    await run_probe(trace_content=companies.traces_content(CompanyKind.SANDBOX))

    traced = traced_text(spans)
    assert "34 ABC 12" in traced and ANSWER in traced


def test_only_synthetic_companies_trace_content() -> None:
    assert [kind for kind in CompanyKind if companies.traces_content(kind)] == [
        CompanyKind.DEMO,
        CompanyKind.SANDBOX,
    ]


def test_an_agent_runs_on_its_own_model_else_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setitem(settings.agent_models, "linker", "openai:a-faster-model")

    assert llm.model_name("linker") == "openai:a-faster-model"
    assert llm.model_name("controller") == settings.default_model
    # A provider named in settings whose client is not installed fails with a reason.
    with pytest.raises(llm.ModelUnavailable):
        llm._model("anthropic:any")


def test_no_module_outside_the_model_layer_imports_a_provider_sdk() -> None:
    # ADR-0005: a provider change is configuration, so only platform/llm.py names one.
    providers = ("openai", "anthropic", "pydantic_ai.models.", "pydantic_ai.providers")
    offenders = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        if relative == "platform/llm.py" or relative.startswith("tests/"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(
                name == p.rstrip(".") or name.startswith(p) for name in names for p in providers
            ):
                offenders.append(relative)
    assert offenders == []
