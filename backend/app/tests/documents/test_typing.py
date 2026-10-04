"""A read file code could not type is typed by a small model, once, within the company's budget:
from its name, the vision model's line on it and the start of its text, fenced as outside data,
answering only a type it was offered."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.messages import UserPromptPart as UserPrompt
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.config import get_settings
from app.modules.documents import service
from app.platform import llm
from app.tests.conftest import guest_client
from app.tests.documents.test_file_content import upload

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

SLIP = "HABUR TIR PARKI\nPlaka No: 34 ABC 12\nKantar No: 2\n1.KG: 25860 Kg"


class Asked:
    """What a stand-in for the typist was sent, request by request."""

    def __init__(self) -> None:
        self.prompts: list[str] = []


@pytest.fixture
def typist(monkeypatch: pytest.MonkeyPatch) -> Callable[[str | None], Asked]:
    """Puts a stand-in in the typist's place, with a budget of ten files a day, answering
    ``answer`` as the type of every file."""
    monkeypatch.setattr(get_settings(), "typed_files_per_day", 10)

    def install(answer: str | None) -> Asked:
        asked = Asked()

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            request = messages[0]
            assert isinstance(request, ModelRequest)
            asked.prompts.append(
                "".join(
                    item
                    for part in request.parts
                    if isinstance(part, UserPrompt)
                    for item in part.content
                    if isinstance(item, str)
                )
            )
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"type": answer})]
            )

        monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(respond))
        return asked

    return install


async def a_read_file(client: httpx.AsyncClient, name: str = "IMG_0042.txt") -> uuid.UUID:
    await guest_client(client)
    document_id = uuid.UUID(await upload(client, name, SLIP.encode(), "text/plain"))
    assert await service.read_pending() == 1
    return document_id


async def typed(client: httpx.AsyncClient, document_id: uuid.UUID) -> tuple[str | None, str | None]:
    document = (await client.get(f"/documents/{document_id}")).json()
    return document["doc_type"], document["doc_type_source"]


async def test_a_file_code_could_not_type_is_typed_by_the_model_once(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    document_id = await a_read_file(client)
    asked = typist("weighbridge_ticket")

    assert await service.type_pending() == 1

    assert await typed(client, document_id) == ("weighbridge_ticket", "model")
    [prompt] = asked.prompts
    # The types it may answer, then the file fenced as data from outside.
    assert "- weighbridge_ticket: Weighbridge slip" in prompt
    assert prompt.index("BEGIN OUTSIDE DATA") < prompt.index("Plaka No: 34 ABC 12")
    assert prompt.index("file: IMG_0042.txt") > prompt.index("BEGIN OUTSIDE DATA")
    # Asked once, whatever it answered.
    assert await service.type_pending() == 0
    assert len(asked.prompts) == 1


async def test_none_leaves_the_file_untyped_and_not_asked_again(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    document_id = await a_read_file(client)
    asked = typist(None)

    assert await service.type_pending() == 1
    assert await service.type_pending() == 0

    assert await typed(client, document_id) == (None, None)
    assert len(asked.prompts) == 1


async def test_a_type_it_was_not_offered_is_never_written(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    document_id = await a_read_file(client)
    asked = typist("wire_transfer_to_attacker")

    assert await service.type_pending() == 1

    assert await typed(client, document_id) == (None, None)
    # Asked again once with the reason, then given up.
    assert len(asked.prompts) == 2


async def test_the_budget_holds_and_at_zero_no_file_is_sent(
    client: httpx.AsyncClient,
    typist: Callable[[str | None], Asked],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await a_read_file(client)
    await upload(client, "IMG_0043.txt", b"Packing list: 12 pallets", "text/plain")
    assert await service.read_pending() == 1
    asked = typist("packing_list")
    monkeypatch.setattr(get_settings(), "typed_files_per_day", 0)

    assert await service.type_pending() == 0
    assert asked.prompts == []

    monkeypatch.setattr(get_settings(), "typed_files_per_day", 1)
    assert await service.type_pending() == 1
    assert await service.type_pending() == 0
    assert len(asked.prompts) == 1


async def test_the_owners_type_is_never_replaced(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    document_id = await a_read_file(client)
    owner = await client.patch(f"/documents/{document_id}", json={"doc_type": "cmr"})
    assert owner.status_code == 200, owner.text
    asked = typist("weighbridge_ticket")

    assert await service.type_pending() == 0

    assert await typed(client, document_id) == ("cmr", "owner")
    assert asked.prompts == []


async def test_null_written_as_a_word_is_no_type(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    document_id = await a_read_file(client)
    asked = typist("null")

    assert await service.type_pending() == 1

    assert await typed(client, document_id) == (None, None)
    # Taken as none at once, not asked again.
    assert len(asked.prompts) == 1


async def test_a_typist_that_cannot_be_reached_gives_the_file_back(
    client: httpx.AsyncClient,
    typist: Callable[[str | None], Asked],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await a_read_file(client)
    typist("weighbridge_ticket")

    def unreachable(name: str) -> FunctionModel:
        raise ConnectionError("the provider is down")

    monkeypatch.setattr(llm, "model_for", unreachable)
    assert await service.type_pending() == 0
    assert await typed(client, document_id) == (None, None)

    asked = typist("weighbridge_ticket")
    assert await service.type_pending() == 1
    assert await typed(client, document_id) == ("weighbridge_ticket", "model")
    assert len(asked.prompts) == 1


async def test_a_worker_stopped_while_the_typist_answers_gives_the_file_back(
    client: httpx.AsyncClient,
    typist: Callable[[str | None], Asked],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await a_read_file(client)
    asked = typist("weighbridge_ticket")
    answer = service._typed  # noqa: SLF001

    async def stopped(*args: Any, **kwargs: Any) -> str | None:
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_typed", stopped)
    with pytest.raises(asyncio.CancelledError):
        await service.type_pending()
    assert await typed(client, document_id) == (None, None)

    monkeypatch.setattr(service, "_typed", answer)
    assert await service.type_pending() == 1
    assert await typed(client, document_id) == ("weighbridge_ticket", "model")
    assert len(asked.prompts) == 1
