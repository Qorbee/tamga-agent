import email.message
import uuid

import httpx
import pytest

from app.modules.conversations import repository, service
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.documents import service as documents
from app.platform import storage
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.documents import archives

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


async def test_search_finds_mail_however_it_is_typed(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    mail = email.message.EmailMessage()
    mail["From"] = "Ayşe Kaya <ops@kaya.example>"
    mail["Subject"] = "Ağustos faturası"
    mail.set_content("Ödeme yapıldı. Plaka 34 ABC 12, yatırım planı ekte.")
    mail.add_alternative('<p>Ödeme yapıldı.</p><img src="cid:logo@kaya">', subtype="html")
    html = mail.get_body(preferencelist=("html",))
    assert html is not None
    html.add_related(PNG, "image", "png", cid="<logo@kaya>", filename="image001.png")
    mail.add_attachment(
        b"%PDF cmr", maintype="application", subtype="pdf", filename="CMR-34ABC12.pdf"
    )
    other = email.message.EmailMessage()
    other["From"] = "x@x.example"
    other["Subject"] = "Başka iş"
    other.set_content("Tır yarın yüklenecek.")
    async with open_session() as db:
        for uid, raw in enumerate((mail.as_bytes(), other.as_bytes()), start=1):
            await service.ingest_mail(
                db,
                company_id=company_id,
                place=service.FolderPlace(
                    mailbox="ops@acme.example", folder="INBOX", uidvalidity=1, sent=False
                ),
                fetched=FetchedMail(uid=uid, raw=raw, internaldate=None),
                own_addresses={"ops@acme.example"},
            )
        await db.commit()

    async def subjects(q: str) -> list[str]:
        page = (await client.get("/messages", params={"q": q})).json()
        return [row["subject"] for row in page["items"]]

    # Any case, Turkish letters or not, words in any order and typed in part.
    assert await subjects("FATURA agustos") == ["Ağustos faturası"]
    assert await subjects("odeme fatu") == ["Ağustos faturası"]
    # A number is found with its spaces and punctuation ignored, in the text or a file's name.
    assert await subjects("34abc12") == ["Ağustos faturası"]
    assert await subjects("cmr-34ABC12.pdf") == ["Ağustos faturası"]
    # A word matches from a word's start: `yatırım` holds `tır` but is no truck.
    assert await subjects("tır") == ["Başka iş"]
    assert await subjects('"ayşe kaya"') == ["Ağustos faturası"]
    assert await subjects('"kaya ayşe"') == []
    # The body's own images are not files beside it; their names would match every mail.
    assert await subjects("image001") == []


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


async def rich_text_mail(company_id: uuid.UUID, uid: int, winmail: bytes) -> None:
    """A mail as Outlook sends "rich text": a bare text part, and a winmail.dat beside it."""
    mail = email.message.EmailMessage()
    mail["From"] = "ops@kaya.example"
    mail["Subject"] = f"Yükleme {uid}"
    mail["Message-ID"] = f"<yukleme-{uid}@kaya>"
    mail.set_content("Yükleme tamam.")
    mail.add_attachment(winmail, maintype="application", subtype="ms-tnef", filename="winmail.dat")
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(
                mailbox="ops@acme.example", folder="INBOX", uidvalidity=1, sent=False
            ),
            fetched=FetchedMail(uid=uid, raw=mail.as_bytes(), internaldate=None),
            own_addresses={"ops@acme.example"},
        )
        await db.commit()


async def test_a_mail_outlook_sent_as_rich_text_shows_the_body_inside_its_winmail_dat(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    wrapped = (
        b"{\\rtf1\\ansi\\ansicpg1254\\fromhtml1 \\deff0{\\fonttbl{\\f0\\fswiss Arial;}}"
        b"{\\*\\htmltag64 <p>}Y\\'fckleme tamam."
        b'{\\*\\htmltag84 <img src="cid:logo@kaya">}{\\*\\htmltag72 </p>}'
        b"{\\*\\htmltag84 <script>steal()</script>}}"
    )
    await rich_text_mail(
        company_id,
        1,
        archives.winmail_of(
            {"logo.png": PNG, "CMR 1.pdf": b"%PDF-1.7 one"},
            rtf_body=archives.stored_rtf(wrapped),
            content_ids={"logo.png": "<logo@kaya>"},
        ),
    )
    [row] = (await client.get("/messages")).json()["items"]

    # Before its files are taken out, the body is shown with no address for the picture it cannot
    # yet draw, as for any `cid:` that names nothing Tamga holds.
    detail = (await client.get(f"/messages/{row['id']}")).json()
    assert detail["body_html"] == "<p>Yükleme tamam.<img></p>"
    assert detail["inline_images"] == {} and detail["body_text"] == "Yükleme tamam."
    [winmail] = detail["attachments"]
    assert (winmail["filename"], winmail["preview"]) == ("winmail.dat", "archive")

    await service.process_pending()
    while await documents.read_pending():
        pass

    parts = (await client.get(f"/documents/{winmail['document_id']}/parts")).json()
    logo = next(part["document"]["id"] for part in parts if part["name"] == "logo.png")
    detail = (await client.get(f"/messages/{row['id']}")).json()
    assert detail["body_html"] == '<p>Yükleme tamam.<img src="cid:logo@kaya"></p>'
    assert detail["inline_images"] == {"logo@kaya": logo}
    # What it holds is sanitized like any mail's HTML.
    assert "script" not in detail["body_html"] and "steal" not in detail["body_html"]


async def test_a_rich_text_mail_with_no_html_inside_shows_its_text(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    rich = archives.stored_rtf(b"{\\rtf1\\ansi\\ansicpg1254 Y\\'fckleme tamam.\\par}")
    await rich_text_mail(company_id, 1, archives.winmail_of({}, rtf_body=rich))
    await rich_text_mail(company_id, 2, b"not a winmail.dat at all")

    for row in (await client.get("/messages")).json()["items"]:
        detail = (await client.get(f"/messages/{row['id']}")).json()
        assert detail["body_html"] is None and detail["body_text"] == "Yükleme tamam."
