import email.message
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversations import service
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.conversations.models import Message
from app.modules.facts import service as facts
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration

ORDER = """New load, order no 4500123.
Truck: CA 1234 AB loads Monday.
Please pay to TR33 0006 1005 1978 6457 8413 26."""

REPLY = (
    """Confirmed, trailer: B-123-XYZ.
________________________________
From: Ayşe Kaya <ayse@kaya.example>
Sent: Monday, September 28, 2026 09:00
Subject: Munich load

"""
    + ORDER
)


def mail(message_id: str, subject: str, body: str, *, delivered_to: str | None = None) -> bytes:
    message = email.message.EmailMessage()
    if delivered_to:
        # What a receiving server adds per mailbox: the same mail, different bytes.
        message["Delivered-To"] = delivered_to
    message["From"] = "ayse@kaya.example"
    message["Subject"] = subject
    message["Date"] = "Mon, 28 Sep 2026 09:00:00 +0300"
    message["Message-ID"] = message_id
    message.set_content(body)
    return message.as_bytes()


async def store(company_id: uuid.UUID, mailbox: str, uid: int, raw: bytes) -> None:
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(mailbox=mailbox, folder="INBOX", uidvalidity=1, sent=False),
            fetched=FetchedMail(uid=uid, raw=raw, internaldate=None),
            own_addresses={"deniz@acme.example"},
        )
        await db.commit()


async def found(company_id: uuid.UUID, message: Message) -> set[tuple[str, str]]:
    async with open_session() as db:
        rows = await facts.facts_of(
            db, company_id=company_id, item_kind=ItemKind.MESSAGE, item_id=message.id
        )
    return {(fact.kind, fact.normalised) for fact in rows}


async def test_a_mail_is_read_once_and_a_reply_only_for_what_it_says(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    await store(
        company_id,
        "ops@acme.example",
        1,
        mail("<o@k>", "Munich load", ORDER, delivered_to="ops@acme.example"),
    )
    await store(
        company_id,
        "deniz@acme.example",
        1,
        mail("<o@k>", "Munich load", ORDER, delivered_to="deniz@acme.example"),
    )
    await store(company_id, "deniz@acme.example", 2, mail("<r@a>", "RE: Munich load", REPLY))

    assert await service.process_pending() == 3
    assert await service.process_pending() == 0

    async with open_session() as db:
        stored = (
            await db.scalars(
                select(Message).where(Message.company_id == company_id).order_by(Message.id)
            )
        ).all()
    first, second, reply = stored
    assert all(message.processed_at is not None for message in stored)
    # The second copy is read as the first was, not again.
    assert (first.copy_of_id, second.copy_of_id) == (None, first.id)
    assert await found(company_id, first) == {
        ("order_number", "4500123"),
        ("plate", "CA1234AB"),
        ("iban", "TR330006100519786457841326"),
    }
    assert await found(company_id, second) == set()
    # The reply's facts come from what it says, not from the order it quotes.
    assert reply.own_text == "Confirmed, trailer: B-123-XYZ."
    assert await found(company_id, reply) == {("plate", "B123XYZ")}


async def test_a_reused_message_id_with_other_text_is_its_own_mail(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    await store(company_id, "ops@acme.example", 1, mail("<same@k>", "Load", "Order no 1111"))
    await store(company_id, "ops@acme.example", 2, mail("<same@k>", "Load", "Order no 2222"))

    await service.process_pending()

    async with open_session() as db:
        stored = (await db.scalars(select(Message).where(Message.company_id == company_id))).all()
    assert [message.copy_of_id for message in stored] == [None, None]


async def test_a_copy_with_another_subject_is_read_for_it(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    body = "Invoice attached."
    await store(company_id, "ops@acme.example", 1, mail("<n@k>", "Invoice no 7001", body))
    await store(company_id, "deniz@acme.example", 1, mail("<n@k>", "Invoice no 7002", body))

    await service.process_pending()

    async with open_session() as db:
        first, second = (
            await db.scalars(
                select(Message).where(Message.company_id == company_id).order_by(Message.id)
            )
        ).all()
    assert second.copy_of_id is None
    assert await found(company_id, second) == {("invoice_number", "7002")}


async def test_a_mail_that_fails_leaves_the_rest_processed_and_waits_an_hour_after_three_tries(
    client: httpx.AsyncClient, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    await store(company_id, "ops@acme.example", 1, mail("<bad@k>", "Load", "Order no 1111"))
    await store(company_id, "ops@acme.example", 2, mail("<good@k>", "Load", "Order no 2222"))
    async with open_session() as db:
        bad = await db.scalar(select(Message.id).where(Message.rfc_message_id == "<bad@k>"))
    read = service.process_message

    async def failing(db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID) -> None:
        if message_id == bad:
            raise RuntimeError("unreadable")
        await read(db, company_id, message_id)

    monkeypatch.setattr(service, "process_message", failing)

    assert await service.process_pending() == 1
    assert await service.process_pending() == 0
    assert await service.process_pending() == 0
    async with open_session() as db:
        failed = await db.get_one(Message, bad)
        assert (failed.processed_at, failed.process_failures) == (None, 3)
    # Three failures and it waits: the next run does not try it, one an hour later does.
    monkeypatch.setattr(service, "process_message", read)
    assert await service.process_pending() == 0
    later = utcnow() + service.PROCESS_RETRY_AFTER + timedelta(minutes=1)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert await service.process_pending() == 1
