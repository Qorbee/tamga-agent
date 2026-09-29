import email.message
import uuid

import httpx
import pytest

from app.modules.conversations import repository, service
from app.modules.conversations.adapters.imap import FetchedMail
from app.platform import storage
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


async def test_the_inbox_lists_mail_newest_first_with_its_files(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    mail = email.message.EmailMessage()
    mail["From"] = "ops@kaya.example"
    mail["Subject"] = "CMR 34 ABC 12"
    mail["Date"] = "Mon, 28 Sep 2026 09:00:00 +0300"
    mail.set_content("Ekte CMR var.")
    mail.add_attachment(b"%PDF cmr", maintype="application", subtype="pdf", filename="cmr.pdf")
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(
                mailbox="ops@acme.example", folder="INBOX", uidvalidity=1, sent=False
            ),
            fetched=FetchedMail(uid=1, raw=mail.as_bytes(), internaldate=None),
            own_addresses={"ops@acme.example"},
        )
        await db.commit()

    page = (await client.get("/messages", params={"q": "cmr"})).json()
    [row] = page["items"]
    detail = (await client.get(f"/messages/{row['id']}")).json()

    assert row["subject"] == "CMR 34 ABC 12" and row["attachment_count"] == 1
    assert row["snippet"].startswith("Ekte CMR var.")
    assert detail["attachments"][0]["filename"] == "cmr.pdf"
    assert detail["attachments"][0]["preview"] == "pdf"
    assert (await client.get("/messages", params={"q": "100%_"})).json()["total"] == 0


async def test_an_html_mail_shows_its_logo_in_the_body_not_as_a_file(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    mail = email.message.EmailMessage()
    mail["From"] = "ops@kaya.example"
    mail["Subject"] = "Fatura"
    mail.set_content("Fatura ekte.")
    mail.add_alternative(
        '<p>Fatura ekte.</p><img src="cid:logo@kaya"><img src="https://t.example/o.gif">',
        subtype="html",
    )
    html = mail.get_body(preferencelist=("html",))
    assert html is not None
    html.add_related(PNG, "image", "png", cid="<logo@kaya>", filename="logo.png")
    mail.add_attachment(b"%PDF fatura", maintype="application", subtype="pdf", filename="f.pdf")
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(
                mailbox="ops@acme.example", folder="INBOX", uidvalidity=1, sent=False
            ),
            fetched=FetchedMail(uid=1, raw=mail.as_bytes(), internaldate=None),
            own_addresses={"ops@acme.example"},
        )
        await db.commit()

    [row] = (await client.get("/messages")).json()["items"]
    detail = (await client.get(f"/messages/{row['id']}")).json()

    assert row["attachment_count"] == 1 and detail["attachment_count"] == 1
    assert {(a["filename"], a["inline"]) for a in detail["attachments"]} == {
        ("logo.png", True),
        ("f.pdf", False),
    }
    [logo] = [a for a in detail["attachments"] if a["inline"]]
    assert '<img src="cid:logo@kaya">' in detail["body_html"]
    assert detail["inline_images"] == {"logo@kaya": logo["document_id"]}
    assert detail["remote_images"] == 1 and detail["body_text"] == "Fatura ekte."

    # Mail stored before the parser knew inline files gets them from the stored original.
    message_id = uuid.UUID(row["id"])
    async with open_session() as db:
        await repository.set_inline(db, company_id, message_id, set())
        assert await service.reread_stored_mail(db, company_id, after=None) == message_id
        await db.commit()
    detail = (await client.get(f"/messages/{row['id']}")).json()
    assert {(a["filename"], a["inline"]) for a in detail["attachments"]} == {
        ("logo.png", True),
        ("f.pdf", False),
    }


async def test_a_mail_whose_original_cannot_be_read_still_shows_its_text(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    mail = email.message.EmailMessage()
    mail["From"] = "ops@kaya.example"
    mail.set_content("Yükleme tamam.")
    mail.add_alternative("<p>Yükleme tamam.</p>", subtype="html")
    raw = mail.as_bytes()
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(
                mailbox="ops@acme.example", folder="INBOX", uidvalidity=1, sent=False
            ),
            fetched=FetchedMail(uid=1, raw=raw, internaldate=None),
            own_addresses={"ops@acme.example"},
        )
        await db.commit()
    await storage.delete(storage.content_key(company_id, raw))
    [row] = (await client.get("/messages")).json()["items"]

    detail = await client.get(f"/messages/{row['id']}")

    assert detail.status_code == 200
    assert detail.json()["body_html"] is None and detail.json()["body_text"] == "Yükleme tamam."
