"""The assistant's search and open, and the helper agents it hands heavy reading to (ADR-0025): one
search over every source of the company and of no other, an open that reaches only what a run was
given and never a file's text, helpers that only read and whose claims stand only on quotes code
finds, and a quiet chat's summary that a later chat's search finds."""

import re
import uuid
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from sqlalchemy import select, update

from app.config import get_settings
from app.modules.assistant import context, helpers, looking
from app.modules.assistant.models import Chat, ChatRun
from app.modules.assistant.seat import Seat
from app.modules.conversations.models import ThreadCategory
from app.modules.documents import service as documents
from app.modules.documents.models import Document, ReadState
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.tests.assistant.test_chats import (
    Script,
    ask,
    model,  # noqa: F401  (the fixture)
    new_chat,
    requests_counted,
    user,
    words,
)
from app.tests.assistant.test_context import sent
from app.tests.conftest import guest_client
from app.tests.linking.test_placing import arrive, mail

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

BODY = (
    "We confirm the load for Monday, steel coils from Istanbul to Munich. Unload at the Hof gate."
)
ORDER = b"Transport order 4411\nLoading: Istanbul, 3 August\n\fPayment: 30 days after delivery\n"


async def a_company_with_mail(client: httpx.AsyncClient) -> uuid.UUID:
    company_id = await guest_client(client)
    await arrive(
        company_id,
        1,
        mail("<order@x>", body=BODY, attach={"order.txt": ORDER}),
        category=ThreadCategory.WORK,
    )
    await documents.read_pending()
    # The test's mail sends every file as a PDF, which this one is not: its text as a reader
    # would have read it.
    async with open_session() as db:
        await db.execute(
            update(Document)
            .where(Document.company_id == company_id)
            .values(text=ORDER.decode(), read_state=ReadState.READ, unreadable_reason=None)
        )
        await db.commit()
    return company_id


async def test_one_search_reaches_every_source_of_the_company_and_of_no_other(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    # Another company's Munich job, which no search of this one reaches.
    await guest_client(client)
    await client.post("/projects", json={"name": "Elsewhere, Ankara to Munich"})
    client.cookies.clear()
    company_id = await a_company_with_mail(client)
    project = (await client.post("/projects", json={"name": "Stone, Istanbul to Munich"})).json()
    taught = await client.post(
        "/memory", json={"kind": "term", "text": "Munich hattı is the weekly Munich line"}
    )
    assert taught.status_code == 201, taught.text
    past = await new_chat(client)
    async with open_session() as db:
        await db.execute(
            update(Chat)
            .where(Chat.id == uuid.UUID(past))
            .values(
                title="The Munich job",
                summary="The owner asked who unloads the Munich job.",
                search_text=" the munich job | the owner asked who unloads the munich job",
            )
        )
        await db.commit()
    script = model(("search", {"query": "Munich"}), "Found it.")

    answer = await ask(client, await new_chat(client), user("What do we have on Munich?"))

    assert words(answer) == "Found it."
    found = script.returned(1)
    assert found.startswith("BEGIN OUTSIDE DATA ")
    assert "[c1]" in found and "Munich load" in found
    assert f"{project['number']}: Stone, Istanbul to Munich" in found
    assert "Munich hattı is the weekly Munich line" in found
    assert "s1:" in found and "The Munich job" in found
    assert "Elsewhere" not in found
    # The chat shows each source's hits by its heading and leaves the handles out
    # (frontend `tool-steps.tsx`): a change to this shape is a change there too.
    for heading in looking._TITLES.values():
        assert f"{heading}:\n" in found
    assert re.search(r"^\[c\d+\] ", found, re.MULTILINE)
    assert re.search(r"^i\d+: |^f\d+: |^p\d+: |^s\d+: ", found, re.MULTILINE)
    # One source, when the run names it.
    script = model(("search", {"query": "order", "sources": ["files"]}), "One file.")
    await ask(client, await new_chat(client), user("Which files are orders?"))
    only = script.returned(1)
    assert "f1: order.txt" in only and "Mail conversations" not in only
    assert company_id


async def test_open_reaches_only_what_the_run_was_given_and_never_a_files_text(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await a_company_with_mail(client)
    script = model(
        # Nothing was given yet: no handle reaches a record.
        ("open", {"handle": "c1"}),
        ("search", {"query": "Munich"}),
        ("open", {"handle": "c1"}),
        ("open", {"handle": "f1"}),
        ("open", {"handle": "f9"}),
        "Read.",
    )

    await ask(client, await new_chat(client), user("What does the Munich mail say?"))

    assert script.returned(1).startswith("No such record: c1 is neither a handle")
    assert BODY in script.returned(3)
    file = script.returned(4)
    assert "order.txt" in file and "Came with the mail of 2026-08-03" in file
    assert "Payment: 30 days" not in file
    assert script.returned(5).startswith("No such record: f9 is neither a handle")


async def test_open_stays_within_its_cap(client: httpx.AsyncClient) -> None:
    company_id = await a_company_with_mail(client)
    seat = Seat(
        company_id=company_id,
        thread_id=None,
        actor=Actor.of_tamga(company_id),
        chat_id=uuid.uuid7(),
        open_cap=40,
    )
    run = RunContext(deps=seat, model=TestModel(), usage=RunUsage())
    await looking.search(run, "Munich", ["mail"])

    opened = await looking.open_record(run, "c1")

    assert "(cut here, at 40 characters" in opened
    assert len(seat.read["c1"].split("\n(cut here")[0]) == 40
    # The fence's label names where the app shows it, its last mail, for the step's link.
    assert re.search(r"^record c1 · view mail:[0-9a-f-]{36}$", opened, re.MULTILINE)


async def test_investigate_reads_in_a_run_of_its_own_and_keeps_only_what_it_cites(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await a_company_with_mail(client)
    script = model(
        ("investigate", {"question": "Where is the Munich load unloaded?"}),
        # The helper's run: a search, an open, and its answer.
        ("search", {"query": "Munich", "sources": ["mail"]}),
        ("open", {"handle": "c1"}),
        (
            "final_result",
            {
                "claims": [
                    {
                        "says": "It is unloaded at the Hof gate.",
                        "cited": [{"source": "c1", "quote": "Unload at the Hof gate"}],
                    },
                    {
                        "says": "It pays in advance.",
                        "cited": [{"source": "c1", "quote": "payment in advance"}],
                    },
                ],
                "missing": "The unloading hour.",
            },
        ),
        "At the Hof gate.",
    )
    spent = await requests_counted(company_id)

    answer = await ask(client, await new_chat(client), user("Where do they unload in Munich?"))

    assert words(answer) == "At the Hof gate."
    # The helper only reads: search and open, and its typed answer.
    assert {tool.name for tool in script.infos[1].function_tools} == {"search", "open"}
    found = script.returned(4)
    assert '- It is unloaded at the Hof gate. [c1: "Unload at the Hof gate"]' in found
    assert "It pays in advance" not in found
    assert "(1 more left out" in found and "Not shown by what it read: The unloading hour." in found
    # The mail it read never entered the chat: only the words it cites.
    assert "steel coils" not in sent(script, 4)
    # The helper's requests count in the day's budget, beside the chat's.
    async with open_session() as db:
        runs = (
            await db.scalars(select(ChatRun.requests).where(ChatRun.company_id == company_id))
        ).all()
    assert 3 in runs and await requests_counted(company_id) > spent


async def test_read_file_answers_from_its_pages_and_drops_a_quote_not_on_its_page(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    await a_company_with_mail(client)
    script = model(
        ("search", {"query": "order", "sources": ["files"]}),
        ("read_file", {"file": "f1", "question": "When is it paid?"}),
        (
            "final_result",
            {
                "facts": [
                    {
                        "says": "Paid 30 days after delivery.",
                        "page": 2,
                        "quote": "30 days after delivery",
                    },
                    # On the file, but not on the page it names.
                    {"says": "Loaded in Istanbul.", "page": 2, "quote": "Loading: Istanbul"},
                ],
                "missing": "",
            },
        ),
        "30 days after delivery.",
    )

    await ask(client, await new_chat(client), user("When is order 4411 paid?"))

    # No tools: the file's pages, marked, are all it is given.
    assert script.infos[2].function_tools == []
    shown = sent(script, 2)
    assert "[page 1]" in shown and "[page 2]" in shown and "Payment: 30 days" in shown
    found = script.returned(3)
    assert '- Paid 30 days after delivery. [page 2: "30 days after delivery"]' in found
    assert "Loaded in Istanbul" not in found and "(1 more left out" in found


async def test_a_claim_stands_on_a_quote_found_in_what_was_read() -> None:
    read = {
        "c3": "Ödeme 30 gün içinde yapılır. Subject: Munich load",
        "P26-014": "P26-014: Stone, İstanbul to Munich",
    }
    cite = helpers.Citation
    claims = [
        # Folded alike: case, accents and the Turkish dots do not matter, nor the handle's case.
        helpers.Claim(
            says="Paid in 30 days", cited=[cite(source="C3", quote="odeme 30 GUN icinde")]
        ),
        helpers.Claim(says="b", cited=[cite(source="p26-014", quote="Stone, Istanbul to Munich")]),
        # A quote short enough to be found almost anywhere shows nothing.
        helpers.Claim(says="c", cited=[cite(source="c3", quote="Subject")]),
        # A quote from a record that was not read.
        helpers.Claim(says="d", cited=[cite(source="c4", quote="Ödeme 30 gün içinde")]),
        # A number the quote does not hold.
        helpers.Claim(
            says="Paid in 45 days", cited=[cite(source="c3", quote="Ödeme 30 gün içinde")]
        ),
    ]

    kept, dropped = helpers.checked(claims, read)

    assert [claim.says for claim in kept] == ["Paid in 30 days", "b"] and dropped == 3


async def test_a_helper_needs_the_days_budget_and_an_answer_starts_three_at_most(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_company_with_mail(client)
    seat = Seat(
        company_id=company_id,
        thread_id=None,
        actor=Actor.of_tamga(company_id),
        chat_id=uuid.uuid7(),
        helped=helpers.HELPS_AN_ANSWER,
    )
    run = RunContext(deps=seat, model=TestModel(), usage=RunUsage())
    assert (await helpers.investigate(run, "Anything?")).startswith("No more helpers")
    # The fixture's day is forty requests; a helper reserves twelve of what is left.
    monkeypatch.setattr(get_settings(), "chat_requests_per_day", 11)
    seat.helped = 0
    assert (await helpers.investigate(run, "Anything?")).startswith(
        "The chat's requests for today are spent"
    )
    assert await requests_counted(company_id) == 0


async def test_a_source_that_fails_leaves_the_others_hits(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = await a_company_with_mail(client)

    async def broken(*args: object) -> list[str]:
        raise RuntimeError("the database fell over")

    monkeypatch.setitem(looking._SEARCHES, "memory", broken)
    seat = Seat(
        company_id=company_id,
        thread_id=None,
        actor=Actor.of_tamga(company_id),
        chat_id=uuid.uuid7(),
    )
    run = RunContext(deps=seat, model=TestModel(), usage=RunUsage())

    found = await looking.search(run, "Munich")

    assert "could not be searched just now" in found and "[c1]" in found
    # A handle reaches its record whatever its case.
    assert "steel coils" in await looking.open_record(run, "C1")


async def test_a_quiet_chat_is_summarized_once_and_a_later_chats_search_finds_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id = await guest_client(client)
    chat_id = await new_chat(client)
    model("It is still a draft.")
    await ask(client, chat_id, user("How is the Stone job?"))
    # A chat still being written in is not summarized.
    assert await context.summarize_quiet() == 0
    async with open_session() as db:
        await db.execute(
            update(Chat)
            .where(Chat.id == uuid.UUID(chat_id))
            .values(last_message_at=utcnow() - context.QUIET - timedelta(minutes=1))
        )
        await db.commit()
    summarizer = model("The owner asked how the Stone job is; Tamga said it is still a draft.")

    assert await context.summarize_quiet() == 1
    assert await context.summarize_quiet() == 0

    assert summarizer.infos[0].function_tools == []
    shown = sent(summarizer, 0)
    assert "Owner: How is the Stone job?" in shown and "Tamga: It is still a draft." in shown
    script = model(
        ("search", {"query": "Stone", "sources": ["chats"]}),
        ("open", {"handle": "s1"}),
        "You asked on the same day.",
    )
    await ask(client, await new_chat(client), user("Did I ask about Stone before?"))
    assert "s1:" in script.returned(1)
    assert "Tamga said it is still a draft" in script.returned(2)
    assert company_id
