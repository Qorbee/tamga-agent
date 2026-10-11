import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from sqlalchemy import func, select, text

from app.config import get_settings
from app.modules.assistant import agents, service, turns
from app.modules.assistant.models import CardKind, Chat, ChatMessage, ChatRecord, ChatRun, Origin
from app.modules.conversations.models import ThreadCategory
from app.modules.linking import service as linking
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource, ProjectStatus
from app.platform import llm
from app.platform.auth import Actor
from app.platform.db import open_session
from app.platform.errors import PermissionDenied
from app.tests.assistant.test_chats import Script, Step, ask, events, user, words
from app.tests.assistant.test_chats import model as owner_asks  # noqa: F401  (the fixture)
from app.tests.conftest import guest_client
from app.tests.linking.test_placing import NEW_PROJECT, answer, arrive, mail, owner_project, tool
from app.tests.linking.test_placing import model as linker  # noqa: F401  (the fixture)
from app.tests.linking.test_placing import placement as placement_of

# The mail these tests store goes to the storage service, in the bucket the tests make.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


@pytest.fixture(autouse=True)
def welcomed(monkeypatch: pytest.MonkeyPatch) -> None:
    """These sandboxes stand in for any company: their welcome is left out, and tested with the
    scenarios (tests/logistics/test_scenarios.py)."""

    async def none(db: object, company_id: object) -> list[turns.Finding]:
        return []

    monkeypatch.setattr(turns, "_opening", none)


READ_ONLY = {
    "waiting",
    # Reading; a helper's run (investigate, read_file) and drafting are a person's.
    "search",
    "open",
}


@pytest.fixture
def tamga(
    owner_asks: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Script]:
    """Puts a scripted stand-in in the model's place for the turns Tamga takes on its own, with
    a budget of forty requests and five new chats a day."""
    monkeypatch.setattr(get_settings(), "tamga_requests_per_day", 40)
    monkeypatch.setattr(get_settings(), "tamga_chats_per_day", 5)

    def install(*steps: Step) -> Script:
        return owner_asks(*steps)

    return install


async def a_proposal(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    *,
    company_id: uuid.UUID | None = None,
    message_id: str = "<order@x>",
    uid: int = 1,
    subject: str = "Munich load",
) -> uuid.UUID:
    """A sandbox in which the linker proposed a project from one conversation."""
    company_id = company_id or await guest_client(client)
    await arrive(company_id, uid, mail(message_id, subject=subject), category=ThreadCategory.WORK)
    linker(tool("search_projects", words="Stone"), NEW_PROJECT)
    assert await linking.place_pending() == 1
    return company_id


async def chats_of(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/chats")).json()["items"])


async def shown(client: httpx.AsyncClient, chat_id: str) -> list[dict[str, Any]]:
    return list((await client.get(f"/chats/{chat_id}/messages")).json()["messages"])


async def spent(company_id: uuid.UUID, began_by: Origin) -> int:
    async with open_session() as db:
        total = await db.scalar(
            select(func.coalesce(func.sum(ChatRun.requests), 0)).where(
                ChatRun.company_id == company_id, ChatRun.began_by == began_by
            )
        )
    return int(total or 0)


async def test_tamga_writes_first_about_a_project_it_proposes(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    company_id = await a_proposal(client, linker)
    proposal = await placement_of(company_id, "<order@x>")
    [project] = (await client.get("/projects")).json()["items"]
    script = tamga(
        ("open", {"handle": project["number"]}),
        "Stone asks for a truck from Istanbul to Munich. It looks like a new job.",
    )

    assert await turns.speak_pending() == 1

    [chat] = await chats_of(client)
    # A chat of Tamga's own, named after what it found, that the owner has not read.
    assert chat["origin"] == "tamga" and chat["unread"] is True and chat["running"] is False
    assert chat["title"] == f"{project['number']} · {project['name']}"
    messages = await shown(client, chat["id"])
    # What code told the run is not a message of the chat; the card comes last, made by code.
    assert [message["role"] for message in messages] == [
        "assistant",
        "tool",
        "assistant",
        "activity",
    ]
    assert messages[2]["content"].startswith("Stone asks for a truck")
    assert messages[3]["activityType"] == CardKind.PROJECT_PROPOSAL
    assert messages[3]["content"] == {"id": project["id"]}

    # The run only reads, and what was found reached it as data from outside.
    assert {tool.name for tool in script.infos[0].function_tools} == READ_ONLY
    [told] = script.said(0)
    # The brief's changing part came before what code told it, and was not kept.
    assert len(script.situations(0)) == 1
    async with open_session() as db:
        kept = await db.scalars(
            select(ChatMessage.message).where(ChatMessage.chat_id == uuid.UUID(chat["id"]))
        )
        stored = str(kept.all())
    assert agents.SITUATION not in stored and proposal.reason in stored
    fence = told.index("BEGIN OUTSIDE DATA")
    assert fence < told.index(project["name"]) < told.index("END OUTSIDE DATA")
    assert proposal.reason in told
    assert await spent(company_id, Origin.TAMGA) == 2
    assert await spent(company_id, Origin.OWNER) == 0

    # It writes about a record once.
    tamga("unused")
    assert await turns.speak_pending() == 0
    assert len(await chats_of(client)) == 1


async def test_a_finding_goes_to_the_chat_that_holds_its_project_or_to_a_new_one(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    owner_asks: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<a@x>", subject="Munich A"), category=ThreadCategory.WORK)
    await arrive(company_id, 2, mail("<b@x>", subject="Munich B"), category=ThreadCategory.WORK)
    first = await owner_project(company_id, "<a@x>", "Munich A")
    second = await owner_project(company_id, "<b@x>", "Munich B")
    # The owner asked about the first project in a chat of their own, which now holds it.
    chat_id = (await client.post("/chats", json={})).json()["id"]
    owner_asks(("open", {"handle": first.number}), "It is a draft.")
    events(await ask(client, chat_id, user("How is Munich A?")))
    await arrive(company_id, 3, mail("<which@x>", subject="POD"), category=ThreadCategory.WORK)
    linker(
        tool("search_projects", words="Munich"),
        answer("ask", alternatives=[first.number, second.number]),
    )
    assert await linking.place_pending() == 3
    question = await placement_of(company_id, "<which@x>")

    tamga("The proof of delivery fits both Munich jobs. Which one is it?")
    assert await turns.speak_pending() == 1

    # No new chat: the question is in the chat that held the project, after what was said.
    [chat] = await chats_of(client)
    assert chat["id"] == chat_id and chat["origin"] == "owner" and chat["unread"] is True
    messages = await shown(client, chat_id)
    assert messages[0]["content"] == "How is Munich A?"
    assert messages[-2]["content"].startswith("The proof of delivery fits both")
    assert messages[-1]["activityType"] == CardKind.CONVERSATION_QUESTION
    assert messages[-1]["content"] == {"id": str(question.id)}

    # The owner archives that chat; the next finding about the project opens a chat of its own.
    assert (await client.patch(f"/chats/{chat_id}", json={"archived": True})).status_code == 200
    await arrive(company_id, 4, mail("<again@x>", subject="CMR"), category=ThreadCategory.WORK)
    linker(
        tool("search_projects", words="Munich"),
        answer("ask", alternatives=[first.number, second.number]),
    )
    assert await linking.place_pending() == 1
    tamga("The consignment note fits both Munich jobs too.")
    assert await turns.speak_pending() == 1

    [opened] = await chats_of(client)
    assert opened["id"] != chat_id and opened["origin"] == "tamga" and opened["title"] == "CMR"


async def test_a_project_merged_into_another_gives_it_its_place_in_the_chats(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    owner_asks: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await a_proposal(client, linker)
    [proposed] = (await client.get("/projects")).json()["items"]
    tamga("Stone asks for a truck to Munich. It looks like a new job.")
    assert await turns.speak_pending() == 1
    [written] = await chats_of(client)
    # The owner knows the job: it is one they opened themselves, which that chat holds too,
    # and which they also asked about in a chat of their own.
    await arrive(company_id, 2, mail("<b@x>", subject="Munich B"), category=ThreadCategory.WORK)
    stays = await owner_project(company_id, "<b@x>", "Munich B")
    owner_asks(("open", {"handle": stays.number}), "It is a draft.")
    events(await ask(client, written["id"], user("Is it Munich B?")))
    own = (await client.post("/chats", json={})).json()["id"]
    owner_asks(("open", {"handle": proposed["number"]}), "It is proposed.")
    events(await ask(client, own, user("What is the proposed one?")))

    merged = await client.post(f"/projects/{proposed['id']}/merge", json={"into": str(stays.id)})
    assert merged.status_code == 200, merged.text

    # The card shows the project that stays, not one that is gone.
    [card] = [each for each in await shown(client, written["id"]) if each["role"] == "activity"]
    assert card["content"] == {"id": str(stays.id)}
    async with open_session() as db:
        held = (
            await db.execute(
                select(ChatRecord.chat_id, ChatRecord.record_id, ChatRecord.raised_at).where(
                    ChatRecord.company_id == company_id, ChatRecord.record_kind == "project"
                )
            )
        ).all()
    # Each chat holds the project that stays once, and Tamga has written about it.
    assert sorted((str(chat_id), record_id) for chat_id, record_id, _ in held) == sorted(
        [(written["id"], stays.id), (own, stays.id)]
    )
    assert {str(chat_id): at is not None for chat_id, _, at in held} == {
        written["id"]: True,
        own: False,
    }


async def test_in_a_chat_tamga_opened_the_owner_asks_about_anything(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    owner_asks: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await a_proposal(client, linker)
    other = (await client.post("/projects", json={"name": "Reed, Izmir to Lyon"})).json()
    tamga("Stone asks for a truck. It looks like a new job.")
    assert await turns.speak_pending() == 1
    [chat] = await chats_of(client)

    script = owner_asks(("open", {"handle": other["number"]}), "Reed is a draft.")
    answered = await ask(client, chat["id"], user("And how is the Reed job?"))

    assert words(answered) == "Reed is a draft."
    # The run is given what Tamga was told and what it wrote, then the question.
    assert len(script.said(0)) == 2 and script.said(0)[1] == "And how is the Reed job?"
    assert "Stone asks for a truck" in str(script.requests[0])
    async with open_session() as db:
        held = await db.execute(
            select(ChatRecord.record_id, ChatRecord.raised_at).where(
                ChatRecord.company_id == company_id
            )
        )
    by_id = {record_id: raised_at for record_id, raised_at in held.all()}
    # The chat holds both projects; only the one Tamga wrote about was raised.
    assert set(by_id) >= {uuid.UUID(other["id"])} and len(by_id) == 2
    assert by_id[uuid.UUID(other["id"])] is None
    assert sum(raised_at is not None for raised_at in by_id.values()) == 1


async def test_a_project_the_owner_rejected_no_longer_waits(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    owner_asks: Callable[..., Script],  # noqa: F811
) -> None:
    await a_proposal(client, linker)
    [project] = (await client.get("/projects")).json()["items"]
    script = owner_asks(("waiting", {}), "One project waits.", ("waiting", {}), "Nothing.")
    chat_id = (await client.post("/chats", json={})).json()["id"]

    await ask(client, chat_id, user("What waits?"))
    assert project["number"] in script.returned(1)

    # Rejected, it keeps the link that was suggested to it, and that link is not asked for.
    rejected = await client.patch(f"/projects/{project['id']}", json={"status": "rejected"})
    assert rejected.status_code == 200, rejected.text
    await ask(client, chat_id, user("And now?", "m2"))
    assert project["number"] not in script.returned(3)
    assert "Nothing waits." in script.returned(3)


async def test_the_days_chats_and_the_budget_bound_what_tamga_opens(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_proposal(client, linker)
    await a_proposal(
        client, linker, company_id=company_id, message_id="<second@x>", uid=2, subject="Lyon load"
    )
    script = tamga("unused")

    # Zero is off: nothing is asked of the model, and nothing is opened, not even the digest.
    monkeypatch.setattr(get_settings(), "tamga_requests_per_day", 0)
    assert await turns.speak_pending() == 0
    assert script.requests == [] and await chats_of(client) == []

    # One chat a day: the older finding gets it, and the other goes into the day's digest.
    monkeypatch.setattr(get_settings(), "tamga_requests_per_day", 40)
    monkeypatch.setattr(get_settings(), "tamga_chats_per_day", 1)
    tamga("The first.")
    assert await turns.speak_pending() == 2
    chats = await chats_of(client)
    assert sorted(chat["digest_on"] is not None for chat in chats) == [False, True]
    assert await spent(company_id, Origin.TAMGA) == 1


async def test_past_the_days_chats_a_finding_waits_in_the_digest_as_its_card(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    owner_asks: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_proposal(client, linker)
    await a_proposal(
        client, linker, company_id=company_id, message_id="<second@x>", uid=2, subject="Lyon load"
    )
    monkeypatch.setattr(get_settings(), "tamga_chats_per_day", 0)
    script = tamga("unused")

    assert await turns.speak_pending() == 2

    # One digest for the day, its cards made by code: no model run, nothing spent.
    [digest] = await chats_of(client)
    assert digest["origin"] == "tamga" and digest["unread"] is True
    assert digest["title"] == "" and digest["digest_on"] is not None
    messages = await shown(client, digest["id"])
    assert [message["activityType"] for message in messages] == [CardKind.PROJECT_PROPOSAL] * 2
    assert script.requests == [] and await spent(company_id, Origin.TAMGA) == 0
    # Each is written about once.
    assert await turns.speak_pending() == 0
    assert len(await shown(client, digest["id"])) == 2

    # A question asked in it does not name it: it keeps its day's name.
    owner_asks("Both are new jobs.")
    assert words(await ask(client, digest["id"], user("What are these?"))) == "Both are new jobs."
    [digest] = await chats_of(client)
    assert digest["title"] == ""


async def test_the_digest_counts_for_no_chat_costs_nothing_and_comes_back_from_the_archive(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_proposal(client, linker)
    monkeypatch.setattr(get_settings(), "tamga_chats_per_day", 0)
    assert await turns.speak_pending() == 1
    [digest] = await chats_of(client)
    archived = await client.patch(f"/chats/{digest['id']}", json={"archived": True})
    assert archived.status_code == 200, archived.text

    # What is new in an archived digest brings it back to the list.
    await a_proposal(
        client, linker, company_id=company_id, message_id="<second@x>", uid=2, subject="Lyon load"
    )
    assert await turns.speak_pending() == 1
    assert [chat["id"] for chat in await chats_of(client)] == [digest["id"]]
    assert len(await shown(client, digest["id"])) == 2

    # The digest is not one of the day's chats: with one allowed, the next finding opens it.
    monkeypatch.setattr(get_settings(), "tamga_chats_per_day", 1)
    monkeypatch.setattr(get_settings(), "tamga_requests_per_day", 1)
    await a_proposal(
        client, linker, company_id=company_id, message_id="<third@x>", uid=3, subject="Pisa load"
    )
    tamga("The third.")
    assert await turns.speak_pending() == 1
    assert sorted(chat["digest_on"] is not None for chat in await chats_of(client)) == [False, True]
    assert await spent(company_id, Origin.TAMGA) == 1

    # The day's requests spent and its chat taken, a finding still reaches the digest, free.
    await a_proposal(
        client, linker, company_id=company_id, message_id="<fourth@x>", uid=4, subject="Bari load"
    )
    assert await turns.speak_pending() == 1
    assert len(await shown(client, digest["id"])) == 3
    assert await spent(company_id, Origin.TAMGA) == 1


async def test_a_project_answered_before_tamga_spoke_is_not_written_about(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    await a_proposal(client, linker)
    [project] = (await client.get("/projects")).json()["items"]
    confirmed = await client.patch(f"/projects/{project['id']}", json={"status": "active"})
    assert confirmed.status_code == 200, confirmed.text
    script = tamga("unused")

    assert await turns.speak_pending() == 0
    assert script.requests == [] and await chats_of(client) == []


async def test_a_replay_company_is_not_written_to(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    company_id = await a_proposal(client, linker)
    async with open_session() as db:
        await db.execute(
            text("UPDATE companies SET mode = 'replay', replay_cursor = now() WHERE id = :id"),
            {"id": company_id},
        )
        await db.commit()
    script = tamga("unused")

    assert await turns.speak_pending() == 0
    assert script.requests == [] and await chats_of(client) == []


async def test_an_answer_that_cannot_be_used_leaves_the_card_alone(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    company_id = await a_proposal(client, linker)
    tamga(UnexpectedModelBehavior("an empty answer"))

    assert await turns.speak_pending() == 1

    [chat] = await chats_of(client)
    assert chat["unread"] is True and chat["running"] is False
    [card] = await shown(client, chat["id"])
    assert card["role"] == "activity" and card["activityType"] == CardKind.PROJECT_PROPOSAL
    # It is not asked again: that would pay again for the same.
    tamga("unused")
    assert await turns.speak_pending() == 0
    assert await spent(company_id, Origin.TAMGA) <= 1


async def test_a_turn_that_fails_leaves_no_chat_and_is_taken_again(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    company_id = await a_proposal(client, linker)
    tamga(ConnectionError("the provider is out of reach"))

    assert await turns.speak_pending() == 0

    assert await chats_of(client) == []
    async with open_session() as db:
        assert await db.scalar(select(func.count()).select_from(Chat)) == 0
    assert await spent(company_id, Origin.TAMGA) == 0

    tamga("Stone asks for a truck. It looks like a new job.")
    assert await turns.speak_pending() == 1
    assert len(await chats_of(client)) == 1


async def test_tamga_waits_while_the_owner_is_answered_in_the_chat(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    company_id = await a_proposal(client, linker)
    tamga("Stone asks for a truck. It looks like a new job.")
    assert await turns.speak_pending() == 1
    [chat] = await chats_of(client)
    project_id = (await shown(client, chat["id"]))[-1]["content"]["id"]
    # A second finding about the same project, while a run is in flight in its chat.
    other = (await client.post("/projects", json={"name": "Reed, Izmir to Lyon"})).json()
    [project] = [
        item for item in (await client.get("/projects")).json()["items"] if item["id"] == project_id
    ]
    await arrive(company_id, 2, mail("<which@x>", subject="POD"), category=ThreadCategory.WORK)
    linker(
        tool("search_projects", words=""),
        answer("ask", alternatives=[project["number"], other["number"]]),
    )
    assert await linking.place_pending() == 1
    async with open_session() as db:
        await db.execute(
            text("UPDATE chats SET running_since = now() WHERE id = :id"), {"id": chat["id"]}
        )
        await db.commit()
    script = tamga("The proof of delivery fits both.")

    assert await turns.speak_pending() == 0
    assert script.requests == []

    # A run that began long ago and never ended is given up: the chat is free again.
    async with open_session() as db:
        await db.execute(
            text("UPDATE chats SET running_since = now() - interval '11 minutes' WHERE id = :id"),
            {"id": chat["id"]},
        )
        await db.commit()
    assert await turns.speak_pending() == 1
    assert len(await chats_of(client)) == 1


async def test_every_finding_is_reached_however_many_wait(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_proposal(client, linker, subject="Munich load")
    await a_proposal(
        client, linker, company_id=company_id, message_id="<later@x>", uid=2, subject="Lyon load"
    )
    # The placements that wait are listed a page at a time, the newest first.
    monkeypatch.setattr(turns, "PAGE", 1)
    monkeypatch.setattr(turns, "TURNS_A_PASS", 1)

    tamga("Stone asks for a truck to Munich.")
    assert await turns.speak_pending() == 1
    tamga("Stone asks for a truck to Lyon.")
    assert await turns.speak_pending() == 1

    # Both were written about, the one that waited longer first.
    written = []
    for chat in sorted(await chats_of(client), key=lambda chat: chat["last_message_at"]):
        [card] = [each for each in await shown(client, chat["id"]) if each["role"] == "activity"]
        written.append(card["content"]["id"])
    first = await placement_of(company_id, "<order@x>")
    later = await placement_of(company_id, "<later@x>")
    assert written == [str(first.project_id), str(later.project_id)]


async def test_a_finding_answered_while_tamga_wrote_leaves_nothing(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_proposal(client, linker)
    [project] = (await client.get("/projects")).json()["items"]
    tamga("Stone asks for a truck. It looks like a new job.")
    run = llm.run

    async def answered_meanwhile(*args: Any, **kwargs: Any) -> Any:
        # The owner rejects the project on the Inbox while the model writes.
        rejected = await client.patch(f"/projects/{project['id']}", json={"status": "rejected"})
        assert rejected.status_code == 200, rejected.text
        return await run(*args, **kwargs)

    monkeypatch.setattr(llm, "run", answered_meanwhile)

    assert await turns.speak_pending() == 0

    # No chat asks for a decision that is no longer open; what the model was asked is paid.
    assert await chats_of(client) == []
    async with open_session() as db:
        assert await db.scalar(select(func.count()).select_from(Chat)) == 0
        assert await db.scalar(select(func.count()).select_from(ChatRecord)) == 0
    assert await spent(company_id, Origin.TAMGA) == 1


async def test_a_chat_left_empty_by_a_worker_that_stopped_is_removed(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
) -> None:
    company_id = await a_proposal(client, linker)
    # A turn opened its chat, and the worker stopped before the model answered.
    async with open_session() as db:
        db.add(Chat(company_id=company_id, origin=Origin.TAMGA, title="Left behind"))
        await db.flush()
        await db.execute(text("UPDATE chats SET running_since = now() - interval '11 minutes'"))
        await db.commit()
    assert [chat["title"] for chat in await chats_of(client)] == ["Left behind"]

    tamga("Stone asks for a truck. It looks like a new job.")
    assert await turns.speak_pending() == 1

    [chat] = await chats_of(client)
    assert chat["title"] != "Left behind" and chat["last_message_at"] is not None


async def test_a_turn_into_a_chat_that_is_nearly_full_compacts_it_first(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],
    owner_asks: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await a_proposal(client, linker)
    [project] = (await client.get("/projects")).json()["items"]
    # The owner asked about the project in a chat, which holds it and has grown full.
    chat_id = (await client.post("/chats", json={})).json()["id"]
    owner_asks(("open", {"handle": project["number"]}), "It is proposed.")
    events(await ask(client, chat_id, user("What is the new project?")))
    async with open_session() as db:
        await db.execute(
            text("UPDATE chats SET context_tokens = :tokens WHERE id = :id"),
            {"tokens": get_settings().chat_context_tokens, "id": chat_id},
        )
        await db.commit()

    script = tamga("The owner asked about the new project.", "Stone asks for a truck to Munich.")
    assert await turns.speak_pending() == 1

    # Notes were written first, from the budget of Tamga's own turns, and the turn was sent
    # them in place of the chat.
    [chat] = await chats_of(client)
    assert chat["id"] == chat_id
    roles = [message["role"] for message in await shown(client, chat_id)]
    assert roles[-3:] == ["activity", "assistant", "activity"]
    told = script.said(1)
    assert "The owner asked about the new project." in told[0]
    assert "What is the new project?" not in " ".join(told)
    assert await spent(company_id, Origin.TAMGA) == 2


async def test_the_actor_of_tamgas_own_turns_changes_nothing(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    actor = Actor.of_tamga(company_id)

    async with open_session() as db:
        with pytest.raises(PermissionDenied):
            await projects.create_project(db, actor, name="A job", number=None)
        with pytest.raises(PermissionDenied):
            await service.create_chat(db, actor, chat_id=None, title="")
        with pytest.raises(PermissionDenied):
            # What any role may do is still a person's to do.
            await service.mark_read(db, actor, uuid.uuid4())
        made = await projects.new_project(
            db,
            company_id=company_id,
            name="A job",
            status=ProjectStatus.PROPOSED,
            source=FieldSource.SUGGESTION,
        )
        await db.commit()
        with pytest.raises(PermissionDenied):
            await projects.update_project(
                db, actor, made.id, name=None, number=None, dates={}, status=ProjectStatus.ACTIVE
            )
        # It reads what the company holds.
        assert (await projects.get_project(db, actor, made.id)).name == "A job"
