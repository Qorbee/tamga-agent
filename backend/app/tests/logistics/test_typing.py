"""A file is typed by code from what is plainly written in it: a CMR by the boxes its form prints
or by its name. Code fills only a type nobody gave, and never one the owner set."""

import struct
import uuid

import httpx
import pytest
from sqlalchemy import update

from app.modules.documents import service
from app.modules.documents.models import Document
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.documents.pdfs import pdf, text
from app.tests.documents.test_file_content import upload

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

# A bilingual CMR form, as its boxes print.
CMR_FORM = pdf(
    text("Lettre de voiture internationale / International consignment note", y=760)
    + text("Lieu et date de la prise en charge de la marchandise", y=730)
    + text("Place and date of taking over the goods: Izmir 28.09.2026", y=710)
    + text("Transporteurs successifs / Successive carriers", y=690)
    + text("Marchandises recues / Goods received", y=670)
)
# An order that names a sender, a consignee and a carrier is not a CMR.
ORDER = pdf(
    text("Transport order 4500123", y=760)
    + text("Sender: Atlas Haulage, Rotterdam", y=730)
    + text("Consignee: Northwind, Munich", y=710)
    + text("Carrier: Mavi Lojistik, truck 34 ABC 12", y=690)
)


async def typed(client: httpx.AsyncClient, document_id: str) -> tuple[str | None, str | None]:
    detail = (await client.get(f"/documents/{document_id}")).json()
    return detail["doc_type"], detail["doc_type_source"]


async def test_a_cmr_is_typed_by_its_boxes_or_its_name(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    form = await upload(client, "scan 0012.pdf", CMR_FORM, "application/pdf")
    named = await upload(
        client,
        "cmr_4471.pdf",
        pdf(text("Received in good order, signed and stamped")),
        "application/pdf",
    )
    order = await upload(client, "order.pdf", ORDER, "application/pdf")

    assert await service.read_pending() == 3

    assert await typed(client, form) == ("cmr", "code")
    assert await typed(client, named) == ("cmr", "code")
    assert await typed(client, order) == (None, None)


async def test_the_owners_type_stands_and_a_cleared_one_is_unknown(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    response = await client.post(
        "/documents",
        params={"filename": "cmr 4471.pdf", "doc_type": "invoice"},
        content=CMR_FORM,
        headers={"content-type": "application/pdf"},
    )
    document_id = response.json()["document"]["id"]

    await service.read_pending()

    assert await typed(client, document_id) == ("invoice", "owner")
    cleared = await client.patch(f"/documents/{document_id}", json={"doc_type": None})
    assert cleared.status_code == 200, cleared.text
    assert await typed(client, document_id) == (None, None)


async def test_files_read_before_the_rules_are_typed_by_the_backfill(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    form = await upload(client, "scan 0012.pdf", CMR_FORM, "application/pdf")
    order = await upload(client, "order.pdf", ORDER, "application/pdf")
    await service.read_pending()
    async with open_session() as db:
        await db.execute(
            update(Document)
            .where(Document.id == uuid.UUID(form))
            .values(doc_type=None, doc_type_source=None)
        )
        await db.commit()

    async with open_session() as db:
        counts = await service.classify_read(db, company_id)
        await db.commit()

    assert counts == {"cmr": 1}
    assert await typed(client, form) == ("cmr", "code")
    assert await typed(client, order) == (None, None)


async def test_a_photo_too_small_to_read_is_typed_by_its_name(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    tiny = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII5B", 13, b"IHDR", 10, 10, 8, 2, 0, 0, 0)
    photo = await upload(client, "cmr_4471.png", tiny + b"\x00" * 16, "image/png")

    await service.read_pending()

    assert await typed(client, photo) == ("cmr", "code")


async def test_a_type_the_owner_saved_while_the_file_was_read_stands(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    form = await upload(client, "scan 0012.pdf", CMR_FORM, "application/pdf")
    async with open_session() as reading:
        [document] = (await service.get_documents(reading, company_id, [uuid.UUID(form)])).values()
        document.text = "lettre de voiture, successive carriers, goods received"
        # Meanwhile the owner types it.
        async with open_session() as owner:
            await owner.execute(
                update(Document)
                .where(Document.id == uuid.UUID(form))
                .values(doc_type="invoice", doc_type_source="owner")
            )
            await owner.commit()

        await service._classify(reading, document)
        await reading.commit()

    assert await typed(client, form) == ("invoice", "owner")
