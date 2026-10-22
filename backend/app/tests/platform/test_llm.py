import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.platform import llm, tracing
from app.platform.language import Language

APP = Path(__file__).resolve().parents[2]
PROMPT = "Plate 34 ABC 12, IBAN TR33 0006 1005 1978 6457 8413 26"
ANSWER = "the answer a pilot's mail produced"


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(tracing.AgentName())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_provider", provider)
    yield exporter
    provider.shutdown()


def traced_text(exporter: InMemorySpanExporter) -> str:
    # An attribute can hold bytes: read as text, so content kept in them is still found.
    return " ".join(
        f"{span.name} {key} {value.decode(errors='replace') if isinstance(value, bytes) else value}"
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
            language=None,
            trace_content=trace_content,
        )
    assert result.output == ANSWER


async def test_a_pilot_run_is_traced_without_its_content(spans: InMemorySpanExporter) -> None:
    await run_probe(trace_content=companies.traces_content(CompanyKind.PILOT))

    traced = traced_text(spans)
    assert spans.get_finished_spans()  # timing, tokens and the model are still traced
    assert "34 ABC 12" not in traced and ANSWER not in traced


async def test_every_span_of_a_run_names_its_agent(spans: InMemorySpanExporter) -> None:
    await run_probe(trace_content=False)

    # Langfuse keeps spans, not traces: a model call is found by its agent only if it says so.
    finished = spans.get_finished_spans()
    assert len(finished) > 1
    assert {(span.attributes or {}).get("langfuse.trace.name") for span in finished} == {"probe"}


def test_spans_go_to_langfuse_on_its_current_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "langfuse_public_key", "pk-lf-x")
    monkeypatch.setattr(get_settings(), "langfuse_secret_key", SecretStr("sk-lf-x"))
    made: list[dict[str, object]] = []

    class Exporter:
        def __init__(self, **arguments: object) -> None:
            made.append(arguments)

        def export(self, spans: object) -> None:  # never reached: nothing is traced here
            raise AssertionError

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 0) -> bool:
            return True

    monkeypatch.setattr(tracing, "OTLPSpanExporter", Exporter)
    try:
        tracing.init_tracing("api")
    finally:
        tracing.shutdown_tracing()

    [arguments] = made
    assert str(arguments["endpoint"]).endswith("/api/public/otel/v1/traces")
    headers = arguments["headers"]
    assert isinstance(headers, dict)
    # Without it the spans take Langfuse's older path, which ends on 2026-11-16.
    assert headers["x-langfuse-ingestion-version"] == "4"
    assert headers["Authorization"].startswith("Basic ")


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
    assert llm.model_name("an-agent-no-setting-names") == settings.default_model
    # A provider named in settings whose client is not installed fails with a reason.
    with pytest.raises(llm.ModelUnavailable):
        llm._model("anthropic:any")


def test_an_agent_reasons_at_its_own_effort_else_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setitem(settings.agent_efforts, "linker", "high")

    assert llm.effort("linker") == "high"
    assert llm.effort("intake") == settings.default_effort == "medium"


async def test_a_run_is_sent_its_effort_beside_the_agents_own_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[AgentInfo] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(info)
        return ModelResponse(parts=[TextPart(ANSWER)])

    # A model that reasons: the library sends an effort to no other.
    reasons = FunctionModel(respond, profile=ModelProfile(supports_thinking=True))
    monkeypatch.setattr(llm, "model_for", lambda name: reasons)
    monkeypatch.setitem(get_settings().agent_efforts, "probe", "high")
    agent: Agent[None, str] = Agent(name="probe", model_settings=ModelSettings(timeout=60))

    await llm.run(agent, PROMPT, deps=None, limits=UsageLimits(request_limit=1), language=None)

    assert sent[0].model_request_parameters.thinking == "high"
    assert sent[0].model_settings == {"timeout": 60}


async def test_a_run_under_another_settings_name_takes_that_names_model_and_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[AgentInfo] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(info)
        return ModelResponse(parts=[TextPart(ANSWER)])

    reasons = FunctionModel(respond, profile=ModelProfile(supports_thinking=True))
    asked: list[str] = []

    def model_for(name: str) -> FunctionModel:
        asked.append(name)
        return reasons

    monkeypatch.setattr(llm, "model_for", model_for)
    monkeypatch.setitem(get_settings().agent_efforts, "assistant", "high")
    monkeypatch.setitem(get_settings().agent_efforts, "speaker", "low")
    agent: Agent[None, str] = Agent(name="assistant")

    await llm.run(
        agent,
        PROMPT,
        deps=None,
        limits=UsageLimits(request_limit=1),
        language=None,
        settings_name="speaker",
    )

    assert asked == ["speaker"]
    assert sent[0].model_request_parameters.thinking == "low"


async def test_a_background_agent_runs_on_the_cheaper_tier_and_on_the_standard_one_when_it_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tiers: list[object] = []
    full = True

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tier = (info.model_settings or {}).get("service_tier")
        tiers.append(tier)
        if tier == "flex" and full:
            # What the provider answers when the tier has no room: nothing was charged.
            raise ModelHTTPError(429, "stand-in", {"error": {"code": "resource_unavailable"}})
        return ModelResponse(parts=[TextPart(ANSWER)])

    monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(respond))
    monkeypatch.setitem(get_settings().agent_tiers, "reader", "flex")
    limits = UsageLimits(request_limit=1)

    waits: Agent[None, str] = Agent(name="reader")
    assert (await llm.run(waits, PROMPT, deps=None, limits=limits, language=None)).output == ANSWER
    assert tiers == ["flex", None]

    full = False
    await llm.run(waits, PROMPT, deps=None, limits=limits, language=None)
    # The account's own limit is the standard tier's too: it is not asked again there.
    limited = ModelHTTPError(429, "stand-in", {"error": {"code": "rate_limit_exceeded"}})

    def refuse(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tiers.append("refused")
        raise limited

    monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(refuse))
    with pytest.raises(ModelHTTPError):
        await llm.run(waits, PROMPT, deps=None, limits=limits, language=None)
    assert tiers.pop() == "refused" and tiers.count("refused") == 0
    monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(respond))
    # An agent the owner waits for is not named, and runs as it always did.
    await llm.run(Agent(name="assistant"), PROMPT, deps=None, limits=limits, language=None)
    assert tiers[2:] == ["flex", None]


async def test_a_hard_case_runs_on_the_agents_second_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "_model", lambda name: TestModel(custom_output_text=name))
    settings = get_settings()
    monkeypatch.setitem(settings.agent_models, "linker", "openai:small")
    monkeypatch.setitem(settings.agent_second_models, "linker", "openai:large")
    limits = UsageLimits(request_limit=1)
    linker: Agent[None, str] = Agent(name="linker")

    first = await llm.run(linker, PROMPT, deps=None, limits=limits, language=None)
    second = await llm.run(linker, PROMPT, deps=None, limits=limits, language=None, second=True)

    assert (first.output, second.output) == ("openai:small", "openai:large")
    assert llm.second_model_name("intake") is None
    with pytest.raises(llm.ModelUnavailable):
        await llm.run(
            Agent(name="intake"), PROMPT, deps=None, limits=limits, language=None, second=True
        )


async def test_a_run_is_told_the_language_its_own_words_are_written_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str | None] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(info.instructions)
        return ModelResponse(parts=[TextPart(ANSWER)])

    monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(respond))
    agent: Agent[None, str] = Agent(name="probe", instructions="Sort the mail.")
    limits = UsageLimits(request_limit=1)

    await llm.run(agent, PROMPT, deps=None, limits=limits, language=Language.TR)
    await llm.run(agent, PROMPT, deps=None, limits=limits, language=None)

    assert sent[0] is not None and sent[0].startswith("Sort the mail.")
    assert "Write in Turkish" in sent[0]
    # An agent that answers in the language it is written to is told none.
    assert sent[1] == "Sort the mail."


def test_a_model_newer_than_the_library_reasons_as_the_one_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test"))
    library = OpenAIProvider(api_key="sk-test")

    for name in llm.SUCCEEDS:
        # Without this the effort an agent is set to never reaches the provider.
        assert llm._model.__wrapped__(f"openai:{name}").profile.get("supports_thinking")
        # The day this fails the installed release knows the model: remove its entry.
        assert not (library.model_profile(name) or {}).get("supports_thinking")
    # The default model is one whose effort is sent, by its own name or through an entry.
    default = get_settings().default_model
    assert llm._model.__wrapped__(default).profile.get("supports_thinking")


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


def test_a_prompts_fingerprint_follows_its_instructions_and_answer_not_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    told: Agent[None, str] = Agent(name="probe", instructions="Name the job.")

    assert llm.fingerprint(told) == llm.fingerprint(
        Agent(name="probe", instructions="Name the job.")
    )
    assert llm.fingerprint(told) != llm.fingerprint(
        Agent(name="probe", instructions="Name the job and its truck.")
    )
    assert llm.fingerprint(told) != llm.fingerprint(
        Agent(name="probe", instructions="Name the job.", output_type=int)
    )

    monkeypatch.setitem(get_settings().agent_models, "probe", "openai:gpt-6-luna")
    monkeypatch.setitem(get_settings().agent_efforts, "probe", "low")
    monkeypatch.setitem(get_settings().agent_second_models, "probe", "openai:gpt-6.1-sol")
    assert llm.made_by(told) == llm.MadeBy("openai:gpt-6-luna", "low", llm.fingerprint(told))
    assert llm.made_by(told, second=True).model == "openai:gpt-6.1-sol"
