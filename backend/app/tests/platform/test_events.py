"""The company event stream: a note reaches the streams of its own company once its write
commits, never another company's and never for a write rolled back; a burst reaches a browser as
one note; and Tamga's own turn is told apart from a run anybody began."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from app.modules.assistant import turns
from app.platform import events
from app.platform.auth import Actor, Role
from app.platform.db import open_session
from app.routers.events import stream_events
from app.tests.assistant.test_chats import Script
from app.tests.assistant.test_chats import model as owner_asks  # noqa: F401  (the fixture)
from app.tests.assistant.test_turns import a_proposal, chats_of
from app.tests.assistant.test_turns import tamga as tamga  # noqa: F401  (the fixture)
from app.tests.linking.test_placing import model as linker  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

WITHIN = 5.0


@pytest.fixture(autouse=True)
async def listening(database: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """No wait for a burst, and the hub's connection closed with the test's event loop."""
    monkeypatch.setattr(events, "_GATHER", 0.0)
    yield
    await events.close()


async def publish(company_id: uuid.UUID, kind: events.EventKind, *, commit: bool = True) -> None:
    async with open_session() as db:
        await events.publish(db, company_id, kind)
        if commit:
            await db.commit()


async def test_a_note_reaches_its_own_company_once_committed() -> None:
    ours, theirs = uuid.uuid4(), uuid.uuid4()
    async with events.stream(ours) as mine, events.stream(theirs) as other:
        await publish(ours, events.EventKind.DOCUMENTS, commit=False)
        await publish(ours, events.EventKind.PROJECTS)
        await publish(ours, events.EventKind.PROJECTS)

        taken = await asyncio.wait_for(mine.take(), WITHIN)

        assert taken == [events.CompanyEvent(kind=events.EventKind.PROJECTS)]
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(other.take(), 0.5)


async def test_a_note_of_tamga_stays_tamgas_when_the_same_chat_moves_again() -> None:
    stream = events.Stream()
    chat_id = uuid.uuid4()
    stream.put(events.CompanyEvent(kind=events.EventKind.CHAT, id=chat_id, by_tamga=True))
    stream.put(events.CompanyEvent(kind=events.EventKind.CHAT, id=chat_id))

    assert await stream.take() == [
        events.CompanyEvent(kind=events.EventKind.CHAT, id=chat_id, by_tamga=True)
    ]


async def test_the_stream_sends_what_changes_in_the_callers_company() -> None:
    company_id = uuid.uuid4()
    actor = Actor(
        user_id=uuid.uuid4(), company_id=company_id, role=Role.OWNER, session_id=uuid.uuid4()
    )
    sent = stream_events(actor)
    first = asyncio.ensure_future(anext(aiter(sent)))
    # The stream is listening once the hub holds it; the hub says so to nobody, so it is asked.
    while company_id not in events._hub._streams:  # noqa: ASYNC110, SLF001
        await asyncio.sleep(0.01)

    await publish(company_id, events.EventKind.INVOICES)

    assert await asyncio.wait_for(first, WITHIN) == events.CompanyEvent(
        kind=events.EventKind.INVOICES
    )
    await sent.aclose()  # type: ignore[attr-defined]
    assert company_id not in events._hub._streams  # noqa: SLF001


async def test_the_stream_needs_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/events")).status_code == 401


async def test_tamga_writing_first_is_told_as_tamgas(
    client: httpx.AsyncClient,
    linker: Callable[..., Any],  # noqa: F811
    tamga: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await a_proposal(client, linker)
    tamga("Stone asks for a truck from Istanbul to Munich.")

    async with events.stream(company_id) as stream:
        assert await turns.speak_pending() == 1
        taken = await asyncio.wait_for(stream.take(), WITHIN)

    [chat] = await chats_of(client)
    assert (
        events.CompanyEvent(kind=events.EventKind.CHAT, id=uuid.UUID(chat["id"]), by_tamga=True)
        in taken
    )
