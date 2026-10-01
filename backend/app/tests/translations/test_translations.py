import json
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.config import get_settings
from app.modules.translations import repository
from app.platform import llm
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


class Stand:
    """A stand-in for the model: answers each text in upper case, and keeps what it was sent."""

    def __init__(self, *, drop: bool = False) -> None:
        self.prompts: list[str] = []
        self.drop = drop

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompt = "".join(
            item
            for part in first.parts
            if isinstance(part, UserPromptPart)
            for item in part.content
            if isinstance(item, str)
        )
        self.prompts.append(prompt)
        lines = [line for line in prompt.splitlines() if line.startswith("[")]
        # The second comes back with the list's number copied in front, as a model may.
        texts = [
            ("[2] " if number == 2 else "") + line.split("] ", 1)[1].upper()
            for number, line in enumerate(lines, 1)
        ]
        if self.drop:
            texts = texts[:-1]
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name=info.output_tools[0].name, args=json.dumps({"texts": texts}))
            ]
        )


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Stand]:
    """Puts a stand-in in the model's place, with a budget of a thousand characters a day."""
    monkeypatch.setattr(get_settings(), "translated_chars_per_day", 1000)

    def install(**how: bool) -> Stand:
        stand = Stand(**how)
        monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(stand.respond))
        return stand

    return install


async def ask(client: httpx.AsyncClient, *texts: str, language: str = "tr") -> dict[str, object]:
    response = await client.post("/translations", json={"language": language, "texts": texts})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_a_text_is_translated_once_and_kept_per_company_and_language(
    client: httpx.AsyncClient, model: Callable[..., Stand]
) -> None:
    await guest_client(client)
    stand = model()

    first = await ask(client, "Stone, Istanbul to Munich", "", "steel coils", "steel coils")
    # In the order asked, an empty one left empty, the same text asked twice translated once.
    assert first == {"texts": ["STONE, ISTANBUL TO MUNICH", "", "STEEL COILS", "STEEL COILS"]}
    [prompt] = stand.prompts
    # The language is code's line; the texts are fenced as data from outside.
    assert prompt.startswith("Write in Turkish.\nBEGIN OUTSIDE DATA ")
    assert "[1] Stone, Istanbul to Munich\n[2] steel coils" in prompt

    # Asked again, nothing is sent; one new text is.
    again = await ask(client, "steel coils", "a new order")
    assert again["texts"] == ["STEEL COILS", "A NEW ORDER"] and len(stand.prompts) == 2
    assert "steel coils" not in stand.prompts[1]
    # Another language is another translation.
    await ask(client, "steel coils", language="en")
    assert stand.prompts[2].startswith("Write in English.")

    # Another company's request finds nothing of this one's.
    await guest_client(client)
    await ask(client, "steel coils")
    assert len(stand.prompts) == 4


async def test_past_the_budget_a_text_comes_back_as_it_was(
    client: httpx.AsyncClient, model: Callable[..., Stand], monkeypatch: pytest.MonkeyPatch
) -> None:
    await guest_client(client)
    stand = model()
    monkeypatch.setattr(get_settings(), "translated_chars_per_day", 12)

    answer = await ask(client, "steel coils", "a much longer text than the budget holds")
    # Null where there is none: the reader shows the text as written.
    assert answer == {"texts": ["STEEL COILS", None]}
    # The budget is spent now: nothing more is sent today.
    assert await ask(client, "one more") == {"texts": [None]} and len(stand.prompts) == 1

    # What is kept costs nothing: it is given whatever the budget.
    monkeypatch.setattr(get_settings(), "translated_chars_per_day", 0)
    assert await ask(client, "steel coils") == {"texts": ["STEEL COILS"]}


async def test_an_answer_with_a_text_missing_is_not_used(
    client: httpx.AsyncClient, model: Callable[..., Stand]
) -> None:
    company_id = await guest_client(client)
    stand = model(drop=True)

    assert await ask(client, "steel coils", "a new order") == {"texts": [None, None]}
    # Sent back to the model twice, then given up.
    assert len(stand.prompts) == 3
    # It was paid for, so it counts against the day and is not sent again today.
    assert await ask(client, "steel coils") == {"texts": [None]} and len(stand.prompts) == 3
    async with open_session() as db:
        spent = await repository.chars_since(db, company_id, utcnow() - timedelta(days=1))
    assert spent == len("steel coils") + len("a new order")


async def test_a_call_that_fails_releases_what_it_reserved(
    client: httpx.AsyncClient, model: Callable[..., Stand], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    model()

    def down(name: str) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise ConnectionError("the provider is down")

        return FunctionModel(respond)

    monkeypatch.setattr(llm, "model_for", down)
    with pytest.raises(ConnectionError):
        await client.post("/translations", json={"language": "tr", "texts": ["steel coils"]})
    async with open_session() as db:
        assert await repository.chars_since(db, company_id, utcnow() - timedelta(days=1)) == 0
    # Nothing is left reserved, so the next request asks the model again.
    stand = model()
    assert (
        await ask(client, "steel coils") == {"texts": ["STEEL COILS"]} and len(stand.prompts) == 1
    )


async def test_a_number_that_belongs_to_the_text_stays(
    client: httpx.AsyncClient, model: Callable[..., Stand]
) -> None:
    await guest_client(client)
    model()
    # The stand-in copies the list's number in front of its second answer; the first text's own
    # bracketed number is the text's.
    answer = await ask(client, "[1] urgent", "steel coils")
    assert answer == {"texts": ["[1] URGENT", "STEEL COILS"]}


async def test_a_request_is_bounded_and_needs_a_session(client: httpx.AsyncClient) -> None:
    assert (
        await client.post("/translations", json={"language": "tr", "texts": []})
    ).status_code == 401
    await guest_client(client)
    long = await client.post("/translations", json={"language": "tr", "texts": ["x" * 601]})
    many = await client.post("/translations", json={"language": "tr", "texts": ["x"] * 101})
    other = await client.post("/translations", json={"language": "de", "texts": ["x"]})
    assert (long.status_code, many.status_code, other.status_code) == (422, 422, 422)
