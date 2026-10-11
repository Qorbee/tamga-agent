"""The company's memory (ADR-0024): only the owner makes an entry hold, a chat only drafts, and
what holds reaches the chat's brief and the background agents as fenced data, nothing else."""

import uuid
from collections.abc import Callable

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.assistant import agents
from app.modules.assistant.models import ChatMessage
from app.modules.memory import service
from app.modules.memory.models import MemoryKind
from app.platform import llm
from app.platform.db import open_session
from app.tests.assistant.test_chats import (
    Script,
    ask,
    model,  # noqa: F401  (the fixture)
    new_chat,
    requests_counted,
    user,
)
from app.tests.conftest import guest_client
from app.tests.identity.test_sessions import PASSWORD, invite_token

pytestmark = pytest.mark.integration

NICKNAME = "Mersin hattı means the client's weekly Mersin to Erbil route"


def instructions(script: Script, request: int) -> str:
    sent = script.requests[request][-1]
    assert isinstance(sent, ModelRequest)
    return sent.instructions or ""


async def words_for(company_id: uuid.UUID) -> str:
    async with open_session() as db:
        return await service.words_for(db, company_id)


async def test_only_the_owner_writes_confirms_and_retires(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": token, "password": PASSWORD})
    written = await client.post("/memory", json={"kind": "term", "text": f"  {NICKNAME} "})
    assert written.status_code == 201, written.text
    entry = written.json()
    assert (entry["state"], entry["source"], entry["text"]) == ("held", "settings", NICKNAME)
    assert entry["confirmed_by"] == entry["written_by"] is not None

    invited = await client.post("/invites", json={"email": "ops@example.com", "role": "ops"})
    client.cookies.clear()
    ops = invited.json()["url"].split("#", 1)[1]
    await client.post("/invites/accept", json={"token": ops, "password": PASSWORD})

    # Another member reads it, and changes nothing.
    listed = (await client.get("/memory", params={"q": "mersin"})).json()
    assert [item["id"] for item in listed["items"]] == [entry["id"]]
    assert (await client.post("/memory", json={"kind": "term", "text": "x"})).status_code == 403
    retire = await client.patch(f"/memory/{entry['id']}", json={"state": "retired"})
    assert retire.status_code == 403
    assert (await client.delete(f"/memory/{entry['id']}")).status_code == 403


async def test_a_chat_drafts_and_only_the_owners_confirmation_lets_agents_read_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    chat_id = await new_chat(client)
    script = model(
        ("remember", {"kind": "alias", "text": NICKNAME}),
        "Noted: it waits for you on its card.",
        "Hello.",
        "Hello again.",
    )

    await ask(client, chat_id, user("Mersin hattı is our weekly Mersin to Erbil route"))

    drafted = script.returned(1)
    # The memory is read once a run: every request of it opens alike, and stays cached. The
    # day and what waits come last, with the question, and are never kept.
    assert instructions(script, 0) == instructions(script, 1)
    [now_is] = script.situations(0)
    assert "Waiting for the owner: 0 bills to approve" in now_is
    shown = (await client.get(f"/chats/{chat_id}/messages")).json()["messages"]
    assert agents.SITUATION not in str(shown)
    async with open_session() as db:
        kept = await db.scalars(
            select(ChatMessage.message).where(ChatMessage.chat_id == uuid.UUID(chat_id))
        )
        assert agents.SITUATION not in str(kept.all())
    [entry] = (await client.get("/memory", params={"chat_id": chat_id})).json()["items"]
    assert entry["id"] in drafted and entry["state"] == "proposed"
    assert entry["written_by"] is None and entry["source"] == "chat"
    # A draft reaches no agent: not the next run's brief, not the linker's words.
    await ask(client, await new_chat(client), user("Hi"))
    assert NICKNAME not in instructions(script, 2)
    assert await words_for(company_id) == ""

    confirmed = await client.patch(f"/memory/{entry['id']}", json={"state": "held"})
    assert confirmed.status_code == 200 and confirmed.json()["confirmed_by"] is not None

    await ask(client, await new_chat(client), user("Hi again"))
    brief = instructions(script, 3)
    assert NICKNAME in brief and "BEGIN OUTSIDE DATA" in brief
    assert NICKNAME in await words_for(company_id)
    used = (await client.get(f"/memory/{entry['id']}")).json()
    assert used["last_used_at"] is not None

    # Retired, it reaches none of them again.
    await client.patch(f"/memory/{entry['id']}", json={"state": "retired"})
    assert await words_for(company_id) == ""


async def test_a_preference_is_the_chats_and_not_the_background_agents(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    said = "Write bill amounts with the currency first"
    await client.post("/memory", json={"kind": "preference", "text": said})
    async with open_session() as db:
        everything = await service.fenced(db, company_id, await service.for_agents(db, company_id))
    assert said in everything
    assert await words_for(company_id) == ""


async def test_an_entry_stays_in_its_company_and_a_draft_is_only_thrown_away(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        draft = await service.draft(
            db,
            company_id,
            kind=MemoryKind.TERM,
            text="Parsiyel means a part load",
            party_id=None,
            project_id=None,
            chat_id=uuid.uuid7(),
            written_by=None,
        )
    held = (
        await client.post("/memory", json={"kind": "term", "text": "Komple: a full truck"})
    ).json()
    assert (await client.delete(f"/memory/{held['id']}")).status_code == 409

    client.cookies.clear()
    await guest_client(client)
    for entry_id in (draft.id, held["id"]):
        assert (await client.get(f"/memory/{entry_id}")).status_code == 404
        assert (
            await client.patch(f"/memory/{entry_id}", json={"state": "held"})
        ).status_code == 404
    assert (await client.get("/memory")).json()["total"] == 0
    # A firm of another company is no subject of this one's memory.
    refused = await client.post(
        "/memory", json={"kind": "alias", "text": "x", "party_id": str(uuid.uuid7())}
    )
    assert refused.status_code == 400


async def test_an_entry_follows_its_project_into_the_one_it_is_merged_into(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    one = (await client.post("/projects", json={"name": "Bursa to Lyon"})).json()
    two = (await client.post("/projects", json={"name": "Bursa to Lyon, again"})).json()
    entry = (
        await client.post(
            "/memory",
            json={"kind": "practice", "text": "Paid in two parts", "project_id": one["id"]},
        )
    ).json()

    merged = await client.post(f"/projects/{one['id']}/merge", json={"into": two["id"]})

    assert merged.status_code == 200, merged.text
    moved = (await client.get(f"/memory/{entry['id']}")).json()
    assert moved["project_id"] == two["id"]
    retired = await client.patch(f"/memory/{entry['id']}", json={"state": "retired"})
    assert retired.status_code == 200


WRITTEN = (
    "Hi! Mersin hattı is what we call the client's weekly Mersin to Erbil route. "
    "Ayşe Hanım runs the client's warehouse, and I want every payment to a new firm asked first."
)
FACTS = [
    {"kind": "alias", "text": "Mersin hattı is the client's weekly Mersin to Erbil route."},
    {"kind": "person", "text": "Ayşe Hanım runs the client's warehouse."},
    {"kind": "preference", "text": "Every payment to a new firm is asked of the owner first."},
]


def memorist(
    monkeypatch: pytest.MonkeyPatch, *answers: list[dict[str, str]]
) -> list[list[ModelMessage]]:
    """Puts a stand-in in the memorist's place that drafts ``answers`` in turn; what it was sent
    is returned, a list per request."""
    sent: list[list[ModelMessage]] = []

    def draft(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(list(messages))
        [output] = info.output_tools
        entries = answers[min(len(sent), len(answers)) - 1]
        return ModelResponse(parts=[ToolCallPart(output.name, {"entries": entries})])

    stand_in = FunctionModel(draft)
    monkeypatch.setattr(llm, "model_for", lambda agent: stand_in)
    return sent


async def test_what_is_written_in_settings_is_drafted_and_holds_only_when_confirmed(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 5)
    sent = memorist(monkeypatch, FACTS)

    drafted = await client.post("/memory/drafts", json={"text": WRITTEN})

    assert drafted.status_code == 201, drafted.text
    items = drafted.json()["items"]
    assert [(item["kind"], item["text"]) for item in items] == [
        (fact["kind"], fact["text"]) for fact in FACTS
    ]
    me = (await client.get("/me")).json()["user"]["id"]
    assert {
        (item["state"], item["source"], item["chat_id"], item["written_by"]) for item in items
    } == {("proposed", "settings", None, me)}
    # What was written reached the model fenced, as data.
    [first] = sent
    prompt = "".join(
        str(part.content)
        for message in first
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )
    assert WRITTEN in prompt and "BEGIN OUTSIDE DATA" in prompt
    # The chat's budget is charged what was asked; a draft reaches no agent.
    assert await requests_counted(company_id) == 1
    assert await words_for(company_id) == ""

    alias = items[0]["id"]
    assert (await client.patch(f"/memory/{alias}", json={"state": "held"})).status_code == 200
    assert "Mersin hattı" in await words_for(company_id)


async def test_drafting_waits_for_the_budget_and_too_many_entries_draft_none(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    sent = memorist(monkeypatch, [FACTS[0]] * (agents.MEMORIST_ENTRIES + 1))

    # Less than one drafting's requests left: the model is never asked.
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 1)
    spent = await client.post("/memory/drafts", json={"text": WRITTEN})
    assert spent.status_code == 429 and sent == []

    # Asked for more than a few facts, twice: nothing is drafted, and both requests are paid.
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 5)
    refused = await client.post("/memory/drafts", json={"text": WRITTEN})
    assert refused.status_code == 409 and len(sent) == 2
    assert (await client.get("/memory")).json()["total"] == 0
    assert await requests_counted(company_id) == 2


async def test_an_entry_with_no_words_is_asked_again_and_nothing_is_half_kept(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 5)
    sent = memorist(monkeypatch, [FACTS[0], {"kind": "term", "text": " "}], [FACTS[0]])

    drafted = await client.post("/memory/drafts", json={"text": WRITTEN})

    assert drafted.status_code == 201, drafted.text
    assert [item["text"] for item in drafted.json()["items"]] == [FACTS[0]["text"]]
    assert len(sent) == 2 and await requests_counted(company_id) == 2
    assert (await client.get("/memory")).json()["total"] == 1
