import email.message

import httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text

from app.config import get_settings
from app.modules.conversations import service as conversations
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.documents import service
from app.platform import storage
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def test_an_upload_is_stored_once_and_typed(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    params = {"filename": "CMR 34 ABC 12.pdf", "doc_type": "cmr", "note": "from the driver"}
    headers = {"content-type": "application/pdf"}

    first = await client.post("/documents", params=params, content=b"%PDF-1.7 a", headers=headers)
    again = await client.post(
        "/documents", params={"filename": "copy.pdf"}, content=b"%PDF-1.7 a", headers=headers
    )
    listed = await client.get("/documents")
    detail = await client.get(f"/documents/{first.json()['document']['id']}")

    assert first.status_code == 201 and first.json()["new_document"] is True
    assert again.json()["new_document"] is False
    assert again.json()["document"]["id"] == first.json()["document"]["id"]
    assert listed.json()["total"] == 1
    document = detail.json()
    assert document["doc_type"] == "cmr" and document["content_type"] == "application/pdf"
    async with httpx.AsyncClient() as browser:
        assert (await browser.get(document["download_url"])).content == b"%PDF-1.7 a"


async def test_an_unknown_type_an_empty_file_and_a_huge_one_are_refused(
    client: httpx.AsyncClient, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await guest_client(client)
    monkeypatch.setattr(get_settings(), "upload_max_bytes", 10)

    unknown = await client.post(
        "/documents", params={"filename": "a", "doc_type": "passport"}, content=b"x"
    )
    empty = await client.post("/documents", params={"filename": "a"}, content=b"")
    huge = await client.post("/documents", params={"filename": "a"}, content=b"x" * 11)

    assert unknown.status_code == 400 and unknown.json()["error"]["field"] == "doc_type"
    assert empty.status_code == 400
    assert huge.status_code == 413


async def test_the_registry_lists_core_and_pack_types(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    types = {t["key"]: t for t in (await client.get("/document-types")).json()}

    assert {"invoice", "bank_receipt", "cmr", "pod_photo"} <= set(types)
    assert types["cmr"]["proof_of_delivery"] is True and types["cmr"]["pack"] == "logistics"


async def test_a_stranger_cannot_list_documents(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/documents")).status_code == 401


async def test_a_file_is_renamed_only_under_its_own_extension(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    await guest_client(client)
    uploaded = await client.post(
        "/documents",
        params={"filename": "scan.PDF", "doc_type": "invoice"},
        content=b"%PDF-1.7 a",
        headers={"content-type": "application/pdf"},
    )
    url = f"/documents/{uploaded.json()['document']['id']}"

    renamed = await client.patch(url, json={"filename": "  August invoice.pdf "})
    other_kind = await client.patch(url, json={"filename": "August invoice.png"})
    no_kind = await client.patch(url, json={"filename": "August invoice"})
    control = await client.patch(url, json={"filename": "a\nb.pdf"})

    assert renamed.status_code == 200
    # Only the name was sent, so the type stays.
    assert renamed.json()["filename"] == "August invoice.pdf"
    assert renamed.json()["doc_type"] == "invoice" and renamed.json()["preview"] == "pdf"
    assert {other_kind.status_code, no_kind.status_code, control.status_code} == {400}
    assert other_kind.json()["error"]["field"] == "filename"
    assert (await client.get(url)).json()["filename"] == "August invoice.pdf"


async def test_an_uploaded_file_is_deleted_with_its_bytes_unless_stored_again(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    headers = {"content-type": "application/pdf"}
    first = await client.post(
        "/documents", params={"filename": "a.pdf"}, content=b"%PDF-1.7 a", headers=headers
    )
    await client.post(
        "/documents", params={"filename": "copy.pdf"}, content=b"%PDF-1.7 a", headers=headers
    )
    document = (await client.get(f"/documents/{first.json()['document']['id']}")).json()
    assert document["held_by"] == []

    deleted = await client.delete(f"/documents/{document['id']}")

    assert deleted.status_code == 204
    assert (await client.get(f"/documents/{document['id']}")).status_code == 404
    assert (await client.get("/documents")).json()["total"] == 0
    async with open_session() as db:
        [job] = (
            await db.execute(
                text(
                    "SELECT args FROM procrastinate_jobs WHERE task_name = 'documents.forget_file'"
                )
            )
        ).scalars()
    assert job == {
        "company_id": str(company_id),
        "sha256": document["sha256"],
        "storage_key": f"{company_id}/{document['sha256']}",
    }

    # The same file uploaded again before the job runs keeps its bytes.
    again = await client.post(
        "/documents", params={"filename": "a.pdf"}, content=b"%PDF-1.7 a", headers=headers
    )
    await service.forget_file(company_id, bytes.fromhex(job["sha256"]), job["storage_key"])
    assert await storage.get(job["storage_key"]) == b"%PDF-1.7 a"

    # Deleted with nothing stored again, they go.
    await client.delete(f"/documents/{again.json()['document']['id']}")
    await service.forget_file(company_id, bytes.fromhex(job["sha256"]), job["storage_key"])
    with pytest.raises(ClientError):
        await storage.get(job["storage_key"])


async def test_a_file_from_a_mail_or_on_a_project_is_not_deleted_nor_a_mails_renamed(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    mail = email.message.EmailMessage()
    mail["From"] = "ops@kaya.example"
    mail["Subject"] = "CMR"
    mail.set_content("Ekte CMR var.")
    mail.add_attachment(b"%PDF cmr", maintype="application", subtype="pdf", filename="cmr.pdf")
    async with open_session() as db:
        await conversations.ingest_mail(
            db,
            company_id=company_id,
            place=conversations.FolderPlace(
                mailbox="ops@acme.example", folder="INBOX", uidvalidity=1, sent=False
            ),
            fetched=FetchedMail(uid=1, raw=mail.as_bytes(), internaldate=None),
            own_addresses={"ops@acme.example"},
        )
        await db.commit()
    # The same bytes uploaded too: the mail still keeps them.
    from_mail = await client.post("/documents", params={"filename": "cmr.pdf"}, content=b"%PDF cmr")
    linked = await client.post("/documents", params={"filename": "pod.pdf"}, content=b"%PDF pod")
    project = await client.post("/projects", json={"name": "Istanbul to Ankara"})
    await client.post(
        "/project-links",
        json={
            "item_kind": "document",
            "item_id": linked.json()["document"]["id"],
            "project_id": project.json()["id"],
            "proof_of_delivery": False,
        },
    )

    for uploaded, holder in ((from_mail, "mail"), (linked, "project")):
        url = f"/documents/{uploaded.json()['document']['id']}"
        assert (await client.get(url)).json()["held_by"] == [holder]
        refused = await client.delete(url)
        assert refused.status_code == 409
        assert (await client.get(url)).status_code == 200

    # A mail's file keeps the sender's name, and can still be typed; a project names nothing.
    mail_url = f"/documents/{from_mail.json()['document']['id']}"
    renamed = await client.patch(mail_url, json={"filename": "Kaya CMR.pdf"})
    assert renamed.status_code == 409 and renamed.json()["error"]["field"] == "filename"
    typed = await client.patch(mail_url, json={"doc_type": "cmr"})
    assert typed.status_code == 200 and typed.json()["filename"] == "cmr.pdf"
    project_url = f"/documents/{linked.json()['document']['id']}"
    assert (await client.patch(project_url, json={"filename": "POD.pdf"})).status_code == 200
