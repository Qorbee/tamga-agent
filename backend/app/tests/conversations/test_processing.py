import email
import email.message
import email.policy
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversations import service
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.conversations.models import Message, MessageAttachment, Thread
from app.modules.documents import service as documents
from app.modules.documents.models import Document
from app.modules.facts import service as facts
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.documents.test_vision import sized_png

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


async def test_a_mail_asks_for_its_files_and_the_pictures_its_body_shows_to_be_read(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    message = email.message.EmailMessage()
    message["From"] = "ayse@kaya.example"
    message["Subject"] = "CMR"
    message["Date"] = "Mon, 28 Sep 2026 09:00:00 +0300"
    message["Message-ID"] = "<cmr@k>"
    message.set_content("CMR attached.")
    message.add_alternative('<p>CMR attached.</p><img src="cid:logo">', subtype="html")
    html = message.get_body(preferencelist=("html",))
    assert isinstance(html, email.message.EmailMessage)
    html.add_related(b"\x89PNG\r\n\x1a\n logo", "image", "png", cid="<logo>")
    message.add_attachment(b"%PDF-1.7 cmr", "application", "pdf", filename="cmr.pdf")
    await store(company_id, "ops@acme.example", 1, message.as_bytes())

    await service.process_pending()

    async with open_session() as db:
        files = (
            await db.execute(
                select(MessageAttachment.inline, Document.read_state)
                .join(Document, Document.id == MessageAttachment.document_id)
                .where(MessageAttachment.company_id == company_id)
            )
        ).all()
    # A picture in the body can be the message itself: the reader, not the mail, says which
    # ones hold nothing.
    assert sorted((inline, state or "") for inline, state in files) == [
        (False, "pending"),
        (True, "pending"),
    ]


async def test_a_picture_in_the_body_is_one_of_its_conversations_files_when_it_can_hold_writing(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    message = email.message.EmailMessage()
    message["From"] = "ayse@kaya.example"
    message["Subject"] = "The truck"
    message["Date"] = "Mon, 28 Sep 2026 09:00:00 +0300"
    message["Message-ID"] = "<plate@k>"
    message.set_content("The plate is below.")
    message.add_alternative(
        '<p>The plate is below.</p><img src="cid:plate"><img src="cid:logo">', subtype="html"
    )
    html = message.get_body(preferencelist=("html",))
    assert isinstance(html, email.message.EmailMessage)
    html.add_related(sized_png(900, 600), "image", "png", cid="<plate>", filename="image001.png")
    html.add_related(sized_png(240, 60), "image", "png", cid="<logo>", filename="image002.png")
    await store(company_id, "ops@acme.example", 1, message.as_bytes())

    await service.process_pending()
    await documents.read_pending()

    async with open_session() as db:
        thread_id = await db.scalar(select(Thread.id).where(Thread.company_id == company_id))
        assert thread_id is not None
        thread = await service.read_thread(db, company_id, thread_id)
    # The photo waits for the vision model and is read with the mail; the logo is furniture.
    assert thread is not None
    assert [file.filename for file in thread.mails[0].files] == ["image001.png"]


def shows(
    message_id: str, picture: bytes, *, beside: bytes | None = None, bulk: bool = False
) -> bytes:
    """A mail whose body shows this picture, with a file beside it when given."""
    message = email.message.EmailMessage()
    if bulk:
        message["List-Unsubscribe"] = "<mailto:leave@news.example>"
    message["From"] = "ayse@kaya.example"
    message["Subject"] = f"Offer {message_id}"
    message["Date"] = "Mon, 28 Sep 2026 09:00:00 +0300"
    message["Message-ID"] = message_id
    message.set_content("Our offer.")
    message.add_alternative('<p>Our offer.</p><img src="cid:banner">', subtype="html")
    html = message.get_body(preferencelist=("html",))
    assert isinstance(html, email.message.EmailMessage)
    html.add_related(picture, "image", "png", cid="<banner>", filename="image001.png")
    if beside is not None:
        message.add_attachment(beside, "text", "plain", filename="terms.txt")
    return message.as_bytes()


async def test_a_newsletters_pictures_are_not_asked_for(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    raw = shows("<news@k>", sized_png(1200, 800), beside=b"Our autumn prices.", bulk=True)
    await store(company_id, "ops@acme.example", 1, raw)

    await service.process_pending()

    async with open_session() as db:
        files = (
            await db.execute(
                select(MessageAttachment.inline, Document.read_state)
                .join(Document, Document.id == MessageAttachment.document_id)
                .where(MessageAttachment.company_id == company_id)
            )
        ).all()
    assert sorted((inline, state or "") for inline, state in files) == [
        (False, "pending"),
        (True, ""),
    ]


async def test_what_many_conversations_carry_belongs_to_none_of_them(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    banner = sized_png(900, 300)
    for uid in range(1, 13):
        raw = shows(f"<offer-{uid}@k>", banner, beside=b"Payment within thirty days.")
        await store(company_id, "ops@acme.example", uid, raw)
    await service.process_pending()

    async def threads() -> list[Thread]:
        async with open_session() as db:
            rows = await db.scalars(select(Thread).where(Thread.company_id == company_id))
            return list(rows.all())

    before = {thread.id: thread.changed_at for thread in await threads()}
    # A banner in every mail's body is a signature, however large: it is not asked for.
    assert await documents.read_pending() == 1

    # The terms sent with every offer were read, and no conversation is looked at again for it.
    assert {thread.id: thread.changed_at for thread in await threads()} == before
    async with open_session() as db:
        states = await db.scalars(
            select(Document.read_state).where(Document.company_id == company_id)
        )
        read = await service.read_thread(db, company_id, next(iter(before)))
    assert sorted(state or "" for state in states) == ["", "read"]
    # Only the file is the mail's.
    assert read is not None and [file.filename for file in read.mails[0].files] == ["terms.txt"]


async def test_a_copy_asks_for_a_file_the_mail_read_before_did_not_have(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    await store(company_id, "ops@acme.example", 1, mail("<cmr@k>", "CMR", "CMR attached."))
    await service.process_pending()
    kept = email.message_from_bytes(
        mail("<cmr@k>", "CMR", "CMR attached."), policy=email.policy.default
    )
    assert isinstance(kept, email.message.EmailMessage)
    kept.add_attachment(b"%PDF-1.7 cmr", "application", "pdf", filename="cmr.pdf")
    await store(company_id, "deniz@acme.example", 1, kept.as_bytes())

    await service.process_pending()

    async with open_session() as db:
        copy = await db.scalar(
            select(Message).where(Message.company_id == company_id, Message.copy_of_id.is_not(None))
        )
        states = await db.scalars(
            select(Document.read_state).where(Document.company_id == company_id)
        )
        assert copy is not None
        assert list(states) == ["pending"]
