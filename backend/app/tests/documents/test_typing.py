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
from app.modules.documents import registry, service
from app.platform import llm
from app.platform.db import open_session
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


async def test_a_file_answered_none_is_asked_again_when_chosen(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    document_id = await a_read_file(client)
    typist(None)
    assert await service.type_pending() == 1
    company_id = uuid.UUID((await client.get("/me")).json()["company"]["id"])

    # A type it may answer now did not exist when it was asked: the file is asked again.
    asked = typist("not_a_paper")
    async with open_session() as db:
        again = await service.answered_none(db, company_id, [document_id])
        assert again == [document_id]
        assert await service.ask_again(db, company_id, again) == 1
        await db.commit()
    assert await service.type_files(company_id, limit=5, only=again) == [
        ("IMG_0042.txt", "not_a_paper")
    ]
    assert await typed(client, document_id) == ("not_a_paper", "model")
    assert "- not_a_paper: Nothing of a job" in asked.prompts[0]
    # Typed now: it is asked about no more.
    async with open_session() as db:
        assert await service.answered_none(db, company_id, [document_id]) == []
        assert await service.ask_again(db, company_id, [document_id]) == 0

    # The owner clears the type: that is the owner's answer, never taken for the typist's none.
    cleared = await client.patch(f"/documents/{document_id}", json={"doc_type": None})
    assert cleared.status_code == 200
    assert await typed(client, document_id) == (None, "owner")
    async with open_session() as db:
        assert await service.answered_none(db, company_id, [document_id]) == []
        assert await service.ask_again(db, company_id, [document_id]) == 0


async def test_files_asked_about_again_are_the_newest_up_to_the_limit(
    client: httpx.AsyncClient, typist: Callable[[str | None], Asked]
) -> None:
    older = await a_read_file(client)
    newer = uuid.UUID(await upload(client, "IMG_0043.txt", SLIP.encode() + b" 2", "text/plain"))
    assert await service.read_pending() == 1
    typist(None)
    assert await service.type_pending() == 2
    company_id = uuid.UUID((await client.get("/me")).json()["company"]["id"])

    async with open_session() as db:
        assert await service.answered_none(db, company_id, [older, newer], limit=1) == [newer]
        assert await service.answered_none(db, company_id, [older, newer]) == [newer, older]


def test_a_business_card_is_an_address_and_a_phone_in_a_few_words() -> None:
    card = "Ayse YILMAZ\nSales\n0 530 111 22 33\nayse@acme.example"
    assert registry.is_a_card(card)
    assert registry.is_a_card("www.acme.example | +90 (324) 111 22 33")
    # No phone, no address, or a page's worth of words: no card.
    assert not registry.is_a_card("Ayse YILMAZ ayse@acme.example")
    assert not registry.is_a_card("Ayse YILMAZ 0 530 111 22 33")
    assert not registry.is_a_card(" ".join([card, *["CMR"] * registry.CARD_WORDS]))
    # A bare run of digits is a phone only after a phone's label: a tax number, an invoice's or a
    # tracking number beside a seller's address is none, and nor is an IBAN or a date and time.
    assert registry.is_a_card("Ayse YILMAZ\nTel: 05301112233\nayse@acme.example")
    assert not registry.is_a_card("ACME Ltd\nVKN 1234567890\ninfo@acme.example\nTotal 1,200.00")
    assert not registry.is_a_card("ACME Ltd\nTR12 0006 4000 0011 2345 6789 01\ninfo@acme.example")
    assert not registry.is_a_card("Sent 05.10.2026 14:30 by info@acme.example")
