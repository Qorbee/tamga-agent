"""The model layer (ADR-0005): which model each agent runs on, and how every agent run is made.

An agent is defined once, in its module's ``agents.py``, with a name and no model. ``run`` gives it
the model settings name for it (``AGENT_MODELS``, else ``DEFAULT_MODEL``), the usage limits its
caller sets, and tracing (ADR-0007). Only this file imports a provider's SDK, so moving an agent
to another provider is configuration.

Tracing content is off unless the caller turns it on for a company whose data is synthetic: a
forgotten flag hides a demo's prompts, never exposes a pilot's.
"""

from collections.abc import Sequence
from functools import cache

from pydantic_ai import Agent, AgentRunResult, UsageLimits
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.messages import UserContent
from pydantic_ai.models import Model
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import get_settings
from app.platform import tracing


class ModelUnavailable(Exception):
    """The agent's model cannot be reached from this configuration (a provider with no key, or
    one whose extra is not installed)."""


def model_name(agent_name: str) -> str:
    settings = get_settings()
    return settings.agent_models.get(agent_name, settings.default_model)


def model_for(agent_name: str) -> Model:
    return _model(model_name(agent_name))


@cache
def _model(model: str) -> Model:
    """One client per model, reused by every run: each opens its own connection pool."""
    provider, _, name = model.partition(":")
    settings = get_settings()
    if provider == "openai":
        if settings.openai_api_key is None:
            raise ModelUnavailable(f"set OPENAI_API_KEY: the model {model} needs it")
        # The Responses API, which streams reasoning summaries to the assistant (ADR-0005).
        return OpenAIResponsesModel(
            name, provider=OpenAIProvider(api_key=settings.openai_api_key.get_secret_value())
        )
    raise ModelUnavailable(f"no client for {provider!r} is installed; add its pydantic-ai extra")


async def run[DepsT, OutputT](
    agent: Agent[DepsT, OutputT],
    prompt: str | Sequence[UserContent],
    *,
    deps: DepsT,
    limits: UsageLimits,
    trace_content: bool = False,
) -> AgentRunResult[OutputT]:
    """Runs ``agent`` once on its configured model. ``trace_content`` sends prompts and answers to
    tracing: only for a company whose data is synthetic (companies ``traces_content``)."""
    if agent.name is None:
        raise ValueError("an agent needs a name: its model and its traces are keyed on it")
    capabilities = []
    provider = tracing.tracer_provider()
    if provider is not None:
        settings = InstrumentationSettings(
            tracer_provider=provider,
            include_content=trace_content,
            include_binary_content=trace_content,
        )
        capabilities.append(Instrumentation(settings=settings))
    return await agent.run(
        prompt,
        deps=deps,
        model=model_for(agent.name),
        usage_limits=limits,
        capabilities=capabilities,
    )
