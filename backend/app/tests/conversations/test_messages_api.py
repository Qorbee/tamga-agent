import email.message

import httpx
import pytest

from app.modules.conversations import service
from app.modules.conversations.adapters.imap import FetchedMail
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


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
