import uuid
from datetime import timedelta

import httpx
import pytest

from app.modules.documents import reading, service
from app.modules.facts import service as facts
from app.platform import storage
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.documents.pdfs import pdf, text
from app.tests.documents.test_file_content import PNG, upload

pytestmark = pytest.mark.integration

ORDER_LINE = "Order no 4500123 for truck 34 ABC 12"


async def document(client: httpx.AsyncClient, document_id: str) -> dict[str, object]:
    detail: dict[str, object] = (await client.get(f"/documents/{document_id}")).json()
    return detail


async def test_an_upload_is_read_once_in_isolation_and_its_facts_recorded(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    invoice = pdf(text("Invoice no INV-2026-118, CMR: 0048213", y=700) + text("IBAN", mode=3))
    order = await upload(client, "fatura.pdf", invoice, "application/pdf")
    photo = await upload(client, "teslim.png", PNG, "image/png")
    word = await upload(client, "talimat.docx", b"PK\x03\x04 a word file", "application/msword")
    broken = await upload(client, "bozuk.pdf", b"%PDF-1.7 a", "application/pdf")
    assert (await document(client, order))["read_state"] == "pending"

    assert await service.read_pending() == 4
    assert await service.read_pending() == 0

    read = await document(client, order)
    assert (read["read_state"], read["unreadable_reason"]) == ("read", None)
    async with open_session() as db:
        found = await facts.facts_of(
            db, company_id=company_id, item_kind=ItemKind.DOCUMENT, item_id=uuid.UUID(order)
        )
        stored = (await service.get_documents(db, company_id, [uuid.UUID(order)]))[uuid.UUID(order)]
    assert {(fact.kind, fact.normalised) for fact in found} == {
        ("invoice_number", "INV2026118"),
        ("cmr_number", "0048213"),
    }
    assert "IBAN" not in stored.text and stored.hidden_parts == 4 and stored.parts == 1
    # A photo waits for the vision model; what no reader knows yet, or cannot open, says why.
    assert (await document(client, photo))["read_state"] == "pending"
    assert (await document(client, word))["unreadable_reason"] == "unsupported"
    assert (await document(client, broken))["unreadable_reason"] == "damaged"


async def test_a_scanned_pdf_waits_for_the_vision_model(
    client: httpx.AsyncClient, storage_bucket: None
) -> None:
    company_id = await guest_client(client)
    scan = await upload(client, "cmr.pdf", pdf(""), "application/pdf")

    await service.read_pending()

    async with open_session() as db:
        stored = (await service.get_documents(db, company_id, [uuid.UUID(scan)]))[uuid.UUID(scan)]
    assert (stored.read_state, stored.needs_vision) == ("pending", True)


async def test_a_file_that_fails_outside_itself_waits_an_hour_after_three_tries(
    client: httpx.AsyncClient, storage_bucket: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await guest_client(client)
    invoice = await upload(client, "fatura.pdf", pdf(text(ORDER_LINE)), "application/pdf")
    get = storage.get

    async def outage(key: str) -> bytes:
        raise ConnectionError("storage is down")

    monkeypatch.setattr(storage, "get", outage)
    for _ in range(3):
        assert await service.read_pending() == 0
    monkeypatch.setattr(storage, "get", get)
    # Three failures: the next run leaves it, one an hour later reads it.
    assert await service.read_pending() == 0
    later = utcnow() + service.READ_RETRY_AFTER + timedelta(minutes=1)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert await service.read_pending() == 1
    assert (await document(client, invoice))["read_state"] == "read"


async def test_a_file_read_in_part_says_so(client: httpx.AsyncClient, storage_bucket: None) -> None:
    await guest_client(client)
    pages = [text(ORDER_LINE)] * (reading.MAX_PAGES + 1)
    long = await upload(client, "uzun.pdf", pdf(*pages), "application/pdf")
    short = await upload(client, "kisa.pdf", pdf(text(ORDER_LINE)), "application/pdf")

    await service.read_pending()

    assert (await document(client, long))["truncated"] is True
    assert (await document(client, short))["truncated"] is False
