"""A project's links carry what each item is, for its timeline: when it came, through which
channel, and the item itself, a file with the mail that brought it."""

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord
from app.modules.conversations import service as conversations
from app.modules.conversations.models import Message, MessageAttachment
from app.modules.documents import service as documents
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.documents import pdfs
from app.tests.documents.archives import zip_of
from app.tests.linking.test_carried_files import proposed
from app.tests.linking.test_placing import (
    Script,
    arrive,
    mail,
    model,  # noqa: F401  (the fixture)
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


async def an_invoice(client: httpx.AsyncClient, company_id: uuid.UUID) -> str:
    await books.store_records(
        company_id,
        "books",
        [],
        [
            InvoiceRecord(
                source_id="sales:1",
                direction=InvoiceDirection.RECEIVABLE,
                kind=InvoiceKind.SALES,
                raw={},
                number="INV-1",
                issue_date=date(2026, 9, 30),
                total_amount=Decimal("1200.00"),
                currency="EUR",
                description="Freight Izmir to Lyon",
            )
        ],
    )
    [invoice] = (await client.get("/invoices")).json()["items"]
    return str(invoice["id"])


async def test_each_link_carries_its_item_when_asked(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model)
    project_id = placement["project_id"]
    invoice_id = await an_invoice(client, uuid.UUID(placement["company_id"]))
    upload = await client.post("/documents", params={"filename": "photo.jpg"}, content=b"jpeg")
    for kind, item_id in (("invoice", invoice_id), ("document", upload.json()["document"]["id"])):
        made = await client.post(
            "/project-links",
            json={"item_kind": kind, "item_id": item_id, "project_id": project_id},
        )
        assert made.status_code == 201, made.text

    page = await client.get("/project-links", params={"project_id": project_id, "expand": "item"})

    items = [link["item"] for link in page.json()["items"]]
    [message] = [item for item in items if item["item_kind"] == "message"]
    files = {
        item["document"]["filename"]: item for item in items if item["item_kind"] == "document"
    }
    [invoice] = [item for item in items if item["item_kind"] == "invoice"]
    assert message["channel"] == "email" and message["at"] == message["message"]["sent_at"]
    # A file came when the mail that brought it was sent, and points to it.
    cmr = files["CMR 4471.pdf"]
    assert (cmr["channel"], cmr["at"]) == ("email", message["at"])
    assert cmr["message_id"] == message["message"]["id"]
    photo = files["photo.jpg"]
    assert (photo["channel"], photo["message_id"]) == ("upload", None)
    assert photo["at"] == photo["document"]["created_at"]
    # An invoice has a day, placed at noon UTC.
    assert (invoice["channel"], invoice["at"]) == ("books", "2026-09-30T12:00:00Z")
    assert invoice["invoice"]["description"] == "Freight Izmir to Lyon"
    plain = await client.get("/project-links", params={"project_id": project_id})
    assert {link["item"] for link in plain.json()["items"]} == {None}


async def test_a_file_from_an_archive_came_with_the_archives_mail(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    packed = {"Papers.zip": zip_of({"POD 4471.pdf": pdfs.pdf("Delivered and signed")})}
    placement = await proposed(client, model, attach=packed)
    await documents.read_pending()

    page = await client.get(
        "/project-links",
        params={"project_id": placement["project_id"], "expand": "item"},
    )

    items = [link["item"] for link in page.json()["items"]]
    [message] = [item for item in items if item["item_kind"] == "message"]
    files = {
        item["document"]["filename"]: item for item in items if item["item_kind"] == "document"
    }
    assert files["POD 4471.pdf"]["message_id"] == message["message"]["id"]
    assert files["POD 4471.pdf"]["channel"] == "email"


async def test_an_invoice_is_read_alone_within_its_company(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    invoice_id = await an_invoice(client, company_id)

    read = await client.get(f"/invoices/{invoice_id}")

    assert read.status_code == 200, read.text
    assert (read.json()["number"], read.json()["description"]) == ("INV-1", "Freight Izmir to Lyon")
    assert (await client.get(f"/invoices/{uuid.uuid4()}")).status_code == 404
    await guest_client(client)
    assert (await client.get(f"/invoices/{invoice_id}")).status_code == 404


async def test_a_file_came_with_the_first_mail_that_brought_it_in_an_archive_or_not(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    pod = pdfs.pdf("Delivered and signed")
    await arrive(
        company_id, 1, mail("<a@x>", attach={"Papers.zip": zip_of({"POD.pdf": pod})}), category=None
    )
    await documents.read_pending()
    later = "Tue, 04 Aug 2026 09:00:00 +0300"
    await arrive(company_id, 2, mail("<b@x>", date=later, attach={"POD.pdf": pod}), category=None)
    async with open_session() as db:
        first = await db.scalar(select(Message.id).where(Message.rfc_message_id == "<a@x>"))
        pod_id = await db.scalar(
            select(MessageAttachment.document_id)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(Message.rfc_message_id == "<b@x>")
        )
        assert first is not None and pod_id is not None

        carriers = await conversations.first_carriers(db, company_id, [pod_id])

    assert carriers[pod_id].message_id == first
