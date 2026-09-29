import email.message
import uuid

import httpx
import pytest

from app.modules.conversations import service
from app.modules.conversations.adapters.imap import FetchedMail
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


def mail(
    message_id: str,
    *,
    sender: str,
    subject: str,
    date: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    delivered_to: str | None = None,
) -> bytes:
    message = email.message.EmailMessage()
    if delivered_to:
        # What a receiving server adds per mailbox: the same mail, different bytes.
        message["Delivered-To"] = delivered_to
    message["From"] = sender
    message["Subject"] = subject
    message["Date"] = date
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(f"{subject} body")
    return message.as_bytes()


async def store(company_id: uuid.UUID, mailbox: str, uid: int, raw: bytes) -> None:
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(mailbox=mailbox, folder="INBOX", uidvalidity=1, sent=False),
            fetched=FetchedMail(uid=uid, raw=raw, internaldate=None),
            own_addresses={"deniz@acme.example", "ops@acme.example"},
        )
        await db.commit()


async def test_replies_and_copies_make_one_conversation(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    order = mail(
        "<order@kaya.example>",
        sender='"Ayşe Kaya" <ayse@kaya.example>',
        subject="Münih yüklemesi",
        date="Mon, 28 Sep 2026 09:00:00 +0300",
    )
    reply = {
        "sender": '"Deniz Arslan" <deniz@acme.example>',
        "subject": "Re: Münih yüklemesi",
        "date": "Mon, 28 Sep 2026 10:00:00 +0300",
        "in_reply_to": "<order@kaya.example>",
        "references": "<order@kaya.example>",
    }
    # A reply that names only its parent, from a client that writes no References.
    short = mail(
        "<short@kaya.example>",
        sender="ayse@kaya.example",
        subject="Re: Münih yüklemesi",
        date="Mon, 28 Sep 2026 11:00:00 +0300",
        in_reply_to="<reply@acme.example>",
    )
    other = mail(
        "<other@x.example>",
        sender="x@x.example",
        subject="Başka iş",
        date="Tue, 29 Sep 2026 08:00:00 +0300",
    )
    await store(company_id, "deniz@acme.example", 1, order)
    await store(company_id, "deniz@acme.example", 2, mail("<reply@acme.example>", **reply))
    await store(
        company_id,
        "ops@acme.example",
        1,
        mail("<reply@acme.example>", delivered_to="ops@acme.example", **reply),
    )
    await store(company_id, "deniz@acme.example", 3, short)
    await store(company_id, "deniz@acme.example", 4, other)

    page = (await client.get("/threads")).json()
    newest, conversation = page["items"]
    mails = (
        await client.get("/messages", params={"thread_key": conversation["thread_key"]})
    ).json()
    searched = (await client.get("/threads", params={"q": "Deniz"})).json()

    assert page["total"] == 2 and newest["subject"] == "Başka iş"
    assert conversation["subject"] == "Münih yüklemesi"
    # The reply's copy in the ops mailbox is stored, and counted once.
    assert conversation["message_count"] == 3 and mails["total"] == 4
    assert conversation["participants"] == ["Ayşe Kaya", "Deniz Arslan", "ayse@kaya.example"]
    assert conversation["latest"]["subject"] == "Re: Münih yüklemesi"
    assert searched["total"] == 1 and searched["items"][0]["message_count"] == 3


async def test_a_reply_read_before_the_mail_it_answers_joins_its_thread_later(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    # The reply names only its parent, which is read after it (another mailbox, a later poll).
    late = mail(
        "<late@kaya.example>",
        sender="ayse@kaya.example",
        subject="Re: Re: Münih yüklemesi",
        date="Mon, 28 Sep 2026 12:00:00 +0300",
        in_reply_to="<reply@acme.example>",
    )
    reply = mail(
        "<reply@acme.example>",
        sender="deniz@acme.example",
        subject="Re: Münih yüklemesi",
        date="Mon, 28 Sep 2026 10:00:00 +0300",
        in_reply_to="<order@kaya.example>",
        references="<order@kaya.example>",
    )
    await store(company_id, "deniz@acme.example", 1, late)
    await store(company_id, "ops@acme.example", 1, reply)

    page = (await client.get("/threads")).json()

    assert page["total"] == 1
    assert page["items"][0]["thread_key"] == "<order@kaya.example>"
    assert page["items"][0]["message_count"] == 2


async def test_two_mails_that_reuse_a_message_id_both_count(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    for uid, date in (
        (1, "Mon, 28 Sep 2026 09:00:00 +0300"),
        (2, "Tue, 29 Sep 2026 09:00:00 +0300"),
    ):
        raw = mail("<same@notify.example>", sender="n@notify.example", subject="Fatura", date=date)
        await store(company_id, "deniz@acme.example", uid, raw)

    [thread] = (await client.get("/threads")).json()["items"]

    assert thread["message_count"] == 2


async def test_a_mail_reusing_a_message_id_moves_no_other_conversation(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    root = mail("<same@x.example>", sender="a@x.example", subject="Sefer", date="Mon, 28 Sep 2026")
    reply = mail(
        "<r@acme.example>",
        sender="deniz@acme.example",
        subject="Re: Sefer",
        date="Tue, 29 Sep 2026",
        in_reply_to="<same@x.example>",
    )
    reuse = mail(
        "<same@x.example>",
        sender="a@x.example",
        subject="Re: Fatura",
        date="Wed, 30 Sep 2026",
        references="<fatura@x.example>",
    )
    for uid, raw in enumerate((root, reply, reuse), start=1):
        await store(company_id, "deniz@acme.example", uid, raw)

    threads = (await client.get("/threads")).json()["items"]

    assert sorted((t["thread_key"], t["message_count"]) for t in threads) == [
        ("<fatura@x.example>", 1),
        ("<same@x.example>", 2),
    ]


async def test_the_original_mail_can_be_downloaded(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    raw = mail("<a@x.example>", sender="a@x.example", subject="CMR", date="Mon, 28 Sep 2026")
    await store(company_id, "deniz@acme.example", 1, raw)
    [row] = (await client.get("/messages")).json()["items"]

    original = await client.get(f"/messages/{row['id']}/raw")

    assert original.content == raw
    assert original.headers["content-type"] == "message/rfc822"
    assert original.headers["content-disposition"].startswith("attachment;")
    assert original.headers["cache-control"] == "no-store"
