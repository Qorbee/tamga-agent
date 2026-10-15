"""The company's memory (ADR-0024): only the owner makes an entry hold, a chat only drafts, and
what holds reaches the chat's brief and the background agents as fenced data, nothing else."""

import uuid
from collections.abc import Callable
from typing import Any

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
        ("remember", {"kind": "alias", "name": "Mersin hattı", "text": NICKNAME}),
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
    "Mersin hattı is what we call the client's weekly Mersin to Erbil route. "
    "Ayşe Hanım runs the client's warehouse, and I want every payment to a new firm asked first."
)
ADDED = [
    {
        "action": "add",
        "kind": "alias",
        "name": "Mersin hattı",
        "text": "Mersin hattı is the client's weekly Mersin to Erbil route.",
    },
    {
        "action": "add",
        "kind": "person",
        "name": "Ayşe Hanım",
        "text": "Ayşe Hanım runs the client's warehouse.",
    },
]
SAID = "Added the route and who runs the warehouse."


def memorist(
    monkeypatch: pytest.MonkeyPatch, *answers: tuple[list[dict[str, str]], str]
) -> list[list[ModelMessage]]:
    """Puts a stand-in in the memorist's place that answers ``answers`` in turn, each its
    changes and its reason; what it was sent is returned, a list per request."""
    sent: list[list[ModelMessage]] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        sent.append(list(messages))
        [output] = info.output_tools
        changes, reason = answers[min(len(sent), len(answers)) - 1]
        return ModelResponse(
            parts=[ToolCallPart(output.name, {"changes": changes, "reason": reason})]
        )

    stand_in = FunctionModel(answer)
    monkeypatch.setattr(llm, "model_for", lambda agent: stand_in)
    return sent


def prompt(sent: list[ModelMessage]) -> str:
    return "".join(
        str(part.content)
        for message in sent
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


async def tell(client: httpx.AsyncClient, text: str, **more: Any) -> httpx.Response:
    return await client.post("/memory/changes", json={"text": text, **more})


async def test_the_owners_words_change_the_memory_at_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 10)
    sent = memorist(monkeypatch, (ADDED, SAID))

    told = await tell(client, WRITTEN)

    assert told.status_code == 200, told.text
    body = told.json()
    assert body["reason"] == SAID
    route, person = (change["entry"] for change in body["changes"])
    me = (await client.get("/me")).json()["user"]["id"]
    assert (route["title"], route["state"], route["source"]) == ("Mersin hattı", "held", "settings")
    assert route["written_by"] == route["confirmed_by"] == me
    # What was written reached the model fenced, as data, after what the memory held.
    assert WRITTEN in prompt(sent[0]) and "BEGIN OUTSIDE DATA" in prompt(sent[0])
    assert "Mersin hattı" in await words_for(company_id)
    assert await requests_counted(company_id) == 1

    # The next text reads the memory by handle: it corrects one entry and retires the other.
    sent = memorist(
        monkeypatch,
        (
            [
                {"action": "change", "entry": "m1", "text": "Mersin hattı runs twice a week."},
                {"action": "retire", "entry": "m2"},
            ],
            "Corrected the route and forgot the warehouse.",
        ),
    )
    edited = await tell(client, "It runs twice a week now; forget Ayşe", entry_id=route["id"])
    assert edited.status_code == 200, edited.text
    assert "m1 [alias] Mersin hattı:" in prompt(sent[0]) and "edit of m1" in prompt(sent[0])
    changed, retired = (change["entry"] for change in edited.json()["changes"])
    assert (changed["id"], changed["text"], changed["title"]) == (
        route["id"],
        "Mersin hattı runs twice a week.",
        "Mersin hattı",
    )
    assert (retired["id"], retired["state"]) == (person["id"], "retired")
    assert "Ayşe" not in await words_for(company_id)

    # A retired entry is used again, or deleted for good; one that holds is never deleted.
    assert (await client.delete(f"/memory/{changed['id']}")).status_code == 409
    assert (await client.delete(f"/memory/{retired['id']}")).status_code == 204
    assert (await client.get(f"/memory/{retired['id']}")).status_code == 404


async def test_a_text_with_nothing_to_keep_changes_nothing_and_says_why(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await guest_client(client)
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 10)
    memorist(monkeypatch, ([], "'test' is not something to remember: tell me a fact instead."))

    told = await tell(client, "test")

    assert told.status_code == 200
    assert told.json() == {
        "changes": [],
        "reason": "'test' is not something to remember: tell me a fact instead.",
    }
    assert (await client.get("/memory")).json()["total"] == 0


async def test_ops_only_draft_and_a_handle_never_given_is_asked_again(
    db: AsyncSession, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await invite_token(db, company="Acme Lojistik", email="owner@example.com")
    await client.post("/invites/accept", json={"token": token, "password": PASSWORD})
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 10)
    invited = await client.post("/invites", json={"email": "ops@example.com", "role": "ops"})
    client.cookies.clear()
    ops = invited.json()["url"].split("#", 1)[1]
    await client.post("/invites/accept", json={"token": ops, "password": PASSWORD})
    sent = memorist(
        monkeypatch,
        ([{"action": "retire", "entry": "m7"}], "Forgot it."),
        (ADDED[:1], "Added the route."),
    )

    told = await tell(client, WRITTEN)

    # Ops may not retire, and m7 was never given: asked again, then only an addition, drafted.
    assert told.status_code == 200, told.text
    assert len(sent) == 2
    [added] = told.json()["changes"]
    assert (added["action"], added["entry"]["state"]) == ("add", "proposed")
    assert (await tell(client, "x", entry_id=added["entry"]["id"])).status_code == 403


async def test_changing_memory_waits_for_the_budget_and_too_many_changes_change_nothing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    sent = memorist(monkeypatch, (ADDED[:1] * (agents.MEMORIST_CHANGES + 1), SAID))

    # Less than one change's requests left: the model is never asked.
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 1)
    spent = await tell(client, WRITTEN)
    assert spent.status_code == 429 and sent == []

    # Asked for more than a few changes, twice: nothing changes, and both requests are paid.
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 5)
    refused = await tell(client, WRITTEN)
    assert refused.status_code == 409 and len(sent) == 2
    assert (await client.get("/memory")).json()["total"] == 0
    assert await requests_counted(company_id) == 2


async def test_a_chat_changes_or_forgets_an_entry_only_as_the_owner_keeps_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await guest_client(client)
    lucy = (
        await client.post(
            "/memory", json={"kind": "personal", "title": "Lucy", "text": "My dog is Lucy"}
        )
    ).json()
    chat_id = await new_chat(client)
    script = model(
        ("remember", {"kind": "personal", "name": "", "text": "My dog is Max", "replaces": 1}),
        ("forget", {"entry": 1}),
        ("remember", {"kind": "personal", "name": "Max", "text": "x", "replaces": 7}),
        ("remember", {"kind": "personal", "name": "", "text": "My dog is Rex", "replaces": 1}),
        "Noted: the card waits for you.",
    )

    await ask(client, chat_id, user("My dog is Max now, not Lucy"))

    # The chat reads the memory by the handles it changes it by.
    assert "m1 personal: My dog is Lucy" in instructions(script, 0)
    drafted = (await client.get("/memory", params={"chat_id": chat_id})).json()["items"]
    [change, rival] = sorted(
        (entry for entry in drafted if entry["replaces_id"] == lucy["id"]),
        key=lambda entry: entry["text"] != "My dog is Max",
    )
    assert (change["state"], change["text"]) == ("proposed", "My dog is Max")
    # Forgetting writes nothing: the card asks the owner.
    assert lucy["id"] in script.returned(2) and "asked" in script.returned(2)
    assert "Use the number of an entry" in script.returned(3)
    assert (await client.get(f"/memory/{lucy['id']}")).json()["state"] == "held"

    kept = await client.patch(f"/memory/{change['id']}", json={"state": "held"})
    assert kept.json()["state"] == "held"
    assert (await client.get(f"/memory/{lucy['id']}")).json()["state"] == "retired"
    # Two changes of one entry never hold side by side: the second finds it changed.
    assert (await client.patch(f"/memory/{rival['id']}", json={"state": "held"})).status_code == 409
