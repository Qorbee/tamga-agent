import uuid
from email.message import EmailMessage

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Mailbox, get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.conversations import service
from app.modules.conversations.mail import ParsedMail, parse_mail
from app.modules.conversations.models import (
    Direction,
    MailFolder,
    Message,
    MessageAttachment,
    MessageLocation,
)
from app.modules.documents.models import Document
from app.platform import storage
from app.tests.conversations.fake_imap import FakeImap, Folder

pytestmark = pytest.mark.integration

OWN = "ops@broker.example"


def raw_mail(sender: str, subject: str, attachment: bytes | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = OWN if sender != OWN else "client@shipper.example"
    message["Subject"] = subject
    message["Date"] = "Mon, 28 Sep 2026 09:15:00 +0300"
    message.set_content(f"{subject}, see the details.")
    if attachment is not None:
        message.add_attachment(
            attachment, maintype="application", subtype="pdf", filename="Fatura 4521.pdf"
        )
    return message.as_bytes()


ORDER = raw_mail("client@shipper.example", "Order 4521")
INVOICE = raw_mail("carrier@example.com", "Invoice for 34 ABC 12", attachment=b"%PDF invoice")
REPLY = raw_mail(OWN, "Re: Order 4521")
# Sent from an alias the account does not log in as: only the Sent folder says the company wrote it.
QUOTE = raw_mail("sales@broker-alias.example", "Teklif 4522")


def mailbox(port: int) -> Mailbox:
    return Mailbox(
        host="127.0.0.1", port=port, username=OWN, password=SecretStr("secret"), tls=False
    )  # noqa: S106 (the fake server's)


async def count(db: AsyncSession, model: type) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


async def test_mail_is_read_once_whatever_folders_it_sits_in(
    db: AsyncSession, storage_bucket: None
) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    await db.commit()
    folders = {
        "INBOX": Folder(uidvalidity=7, messages={1: ORDER, 2: INVOICE}),
        "Sent": Folder(
            uidvalidity=3, messages={1: REPLY, 2: QUOTE}, flags="\\HasNoChildren \\Sent"
        ),
        "Proje 4521": Folder(uidvalidity=9, messages={5: ORDER}),  # the owner filed a copy
        "Trash": Folder(
            uidvalidity=4,
            messages={1: raw_mail("x@spam.example", "Win!")},
            flags="\\HasNoChildren \\Trash",
        ),
    }

    with FakeImap(folders) as fake:
        first = await service.poll_mailbox(
            mailbox(fake.port), company_id=company.id, own_addresses={OWN}
        )
        again = await service.poll_mailbox(
            mailbox(fake.port), company_id=company.id, own_addresses={OWN}
        )
        fake.folders["INBOX"] = Folder(uidvalidity=8, messages={11: ORDER, 12: INVOICE})
        renumbered = await service.poll_mailbox(
            mailbox(fake.port), company_id=company.id, own_addresses={OWN}
        )

    # Four distinct mails; the filed copy and the renumbered INBOX add places, not messages.
    assert (first, again, renumbered) == (4, 0, 0)
    assert await count(db, Message) == 4
    assert await count(db, MessageLocation) == 7
    assert await count(db, MailFolder) == 3  # Trash is never read

    # Nothing but reads reached the server, and every fetch peeked.
    assert fake.verbs() <= {
        "CAPABILITY",
        "LOGIN",
        "LIST",
        "EXAMINE",
        "UID SEARCH",
        "UID FETCH",
        "LOGOUT",
    }
    fetches = [c for c in fake.commands if c.upper().startswith("UID FETCH")]
    assert fetches and all("BODY.PEEK[]" in c for c in fetches)

    reply = await db.scalar(select(Message).where(Message.subject == "Re: Order 4521"))
    assert reply is not None and reply.direction is Direction.OUTBOUND
    quote = await db.scalar(select(Message).where(Message.subject == "Teklif 4522"))
    assert quote is not None and quote.direction is Direction.OUTBOUND
    invoice = await db.scalar(select(Message).where(Message.subject == "Invoice for 34 ABC 12"))
    assert invoice is not None and invoice.direction is Direction.INBOUND
    assert invoice.raw_key is not None and await storage.get(invoice.raw_key) == INVOICE

    attachment = await db.scalar(select(MessageAttachment))
    document = await db.scalar(select(Document))
    assert attachment is not None and document is not None
    assert attachment.message_id == invoice.id and attachment.filename == "Fatura 4521.pdf"
    assert await storage.get(document.storage_key) == b"%PDF invoice"


async def test_a_backfill_spreads_over_polls_and_resumes_where_it_stopped(
    db: AsyncSession, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    await db.commit()
    monkeypatch.setattr(get_settings(), "mail_batch", 2)
    inbox = Folder(
        uidvalidity=7, messages={n: raw_mail(f"c{n}@x.example", f"Mail {n}") for n in range(1, 6)}
    )

    with FakeImap({"INBOX": inbox}) as fake:
        polls = [
            await service.poll_mailbox(
                mailbox(fake.port), company_id=company.id, own_addresses={OWN}
            )
            for _ in range(4)
        ]

    assert polls == [2, 2, 1, 0]
    assert await count(db, Message) == 5


async def test_a_mail_the_parser_cannot_read_is_kept_raw_and_the_folder_moves_on(
    db: AsyncSession, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    await db.commit()

    def parse_or_choke(raw: bytes) -> ParsedMail:
        if raw == INVOICE:
            raise ValueError("a mail no parser can read")
        return parse_mail(raw)

    monkeypatch.setattr(service, "parse_mail", parse_or_choke)

    with FakeImap({"INBOX": Folder(uidvalidity=7, messages={1: INVOICE, 2: ORDER})}) as fake:
        stored = await service.poll_mailbox(
            mailbox(fake.port), company_id=company.id, own_addresses={OWN}
        )

    assert stored == 2
    unread = await db.scalar(select(Message).where(Message.subject.is_(None)))
    assert unread is not None and unread.raw_key is not None
    assert await storage.get(unread.raw_key) == INVOICE


async def test_an_unknown_pilot_company_fails_the_poll_loudly(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "pilot_company_id", uuid.uuid7())
    monkeypatch.setattr(settings, "mailboxes", [mailbox(1)])

    with pytest.raises(RuntimeError, match="PILOT_COMPANY_ID"):
        await service.poll_pilot_mailboxes()


async def test_a_failing_mailbox_is_logged_without_its_full_address(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    company = await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)
    await db.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "pilot_company_id", company.id)
    with FakeImap({}) as fake:
        closed = fake.port  # nothing listens here once the fake has stopped
    monkeypatch.setattr(settings, "mailboxes", [mailbox(closed)])

    assert await service.poll_pilot_mailboxes() == 0
    assert "mail poll of op***@broker.example on 127.0.0.1 failed" in caplog.text
    assert OWN not in caplog.text
