"""The bill reader reads what a bill charges for from its file, and code keeps only what the file
holds: a line's words, its plates, and whether the lines add up."""

import dataclasses
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select, update

from app.config import get_settings
from app.modules.books import service as books
from app.modules.books.agents import BillRead
from app.modules.books.models import Invoice, InvoiceDirection, InvoiceKind
from app.modules.books.reading import checked
from app.modules.books.sources import InvoiceRecord
from app.modules.conversations.models import MessageAttachment
from app.modules.documents import service as documents
from app.modules.facts.models import Fact
from app.platform import llm
from app.platform.db import open_session
from app.tests.conftest import guest_client
from app.tests.documents.pdfs import pdf, text
from app.tests.linking.test_placing import Script, arrive, mail

FILE = """FREIGHT INVOICE 2026-118
Freight Gebze - Zakho, truck 34 AB 1234    1,000.00 EUR
Waiting at the border, 2 days, truck 34 AB 1234    300.00 EUR
Freight Gebze - Zakho, truck 06 XY 99    1,000.00 EUR
Net 2,300.00 EUR"""

# The file as a PDF of one page, a line of text a line of the bill.
BILL_PDF = pdf("".join(text(line, y=700 - 20 * at) for at, line in enumerate(FILE.splitlines())))

LINES: list[dict[str, Any]] = [
    {
        "quote": "Freight Gebze - Zakho, truck 34 AB 1234",
        "charge": "freight",
        "amount": "1000.00",
        "currency": "EUR",
        "plates": ["34 AB 1234"],
        "origin": "Gebze",
        "destination": "Zakho",
    },
    {
        "quote": "Waiting at the border, 2 days, truck 34 AB 1234",
        "charge": "waiting",
        "amount": "300.00",
        "currency": "EUR",
        # A plate the file does not hold is not kept.
        "plates": ["34 AB 1234", "34 ZZ 999"],
        "days": 2,
    },
    {
        "quote": "Freight  Gebze - Zakho, truck 06 XY 99",
        "charge": "transport",
        "amount": "1000.00",
        "currency": "EUR",
        "plates": ["06XY99"],
    },
    # Words the file does not have: the line is left out.
    {"quote": "Fuel surcharge", "charge": "other", "amount": "50.00", "currency": "EUR"},
]
READ: dict[str, Any] = {
    "about": "Freight Gebze → Zakho for two trucks, with two days' waiting",
    "kind": "invoice",
}


def test_code_keeps_a_line_only_with_its_words_and_plates_in_the_file() -> None:
    read = BillRead.model_validate({**READ, "lines": LINES})
    kept = checked(read, FILE, currency="EUR", net=Decimal("2300.00"), total=Decimal("2760.00"))
    assert [line.quote for line in kept.lines] == [line["quote"] for line in LINES[:2]] + [
        "Freight Gebze - Zakho, truck 06 XY 99"
    ]
    assert [line.plates for line in kept.lines] == [["34AB1234"], ["34AB1234"], ["06XY99"]]
    assert kept.dropped == 1 and kept.balance is True
    # Lines that add up to neither figure say so.
    short = checked(read, FILE, currency="EUR", net=Decimal("2600.00"), total=None)
    assert short.balance is False
    # In another currency than the bill's, they are not added up at all.
    assert checked(read, FILE, currency="TRY", net=Decimal("2300"), total=None).balance is None


def test_code_keeps_a_lines_figures_places_and_plates_only_from_its_own_row() -> None:
    line = {
        "quote": "Waiting at the border",
        "charge": "waiting",
        # Neither on the row: an amount another row has, and days it does not say.
        "amount": "1000.00",
        "days": 3,
        # A place another row names, and a plate the row holds only as part of a longer one.
        "origin": "Gebze",
        "plates": ["34 AB 12", "34 AB 1234"],
    }
    [kept] = checked(
        BillRead.model_validate({**READ, "lines": [line]}),
        FILE,
        currency="EUR",
        net=None,
        total=None,
    ).lines
    assert (kept.amount, kept.days, kept.origin, kept.plates) == (None, None, None, ["34AB1234"])
    # A figure written the other way, "1.000,00", is the same amount.
    written = FILE.replace("1,000.00", "1.000,00")
    [first] = checked(
        BillRead.model_validate({**READ, "lines": LINES[:1]}),
        written,
        currency="EUR",
        net=None,
        total=None,
    ).lines
    assert first.amount == Decimal("1000.00") and first.origin == "Gebze"


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Script]:
    monkeypatch.setattr(get_settings(), "bills_read_per_day", 5)

    def install(*steps: Any) -> Script:
        script = Script(*steps)
        monkeypatch.setattr(llm, "model_for", lambda name: FunctionModel(script.respond))
        return script

    return install


@pytest.mark.integration
@pytest.mark.usefixtures("storage_bucket")
async def test_a_bill_with_a_file_is_read_once_for_its_lines_and_plates(
    client: httpx.AsyncClient, reader: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<bill@x>", attach={"bill.pdf": BILL_PDF}), category=None)
    record = InvoiceRecord(
        source_id="purchase:1",
        direction=InvoiceDirection.PAYABLE,
        kind=InvoiceKind.PURCHASE,
        raw={},
        number="2026-118",
        issue_date=date(2026, 10, 1),
        currency="EUR",
        net_amount=Decimal("2300.00"),
        total_amount=Decimal("2760.00"),
    )
    await books.store_records(company_id, "books", [], [record])
    # Its file is read first: until then the reader waits for it.
    await documents.read_pending()
    async with open_session() as db:
        file_id = await db.scalar(
            select(MessageAttachment.document_id).where(MessageAttachment.company_id == company_id)
        )
        bill_id = await db.scalar(select(Invoice.id).where(Invoice.company_id == company_id))
        await db.execute(update(Invoice).where(Invoice.id == bill_id).values(document_id=file_id))
        await db.commit()
    assert bill_id is not None

    script = reader(("answer", {**READ, "lines": LINES}))
    done = await books.read_pending_bills()
    assert (done.read, done.dropped) == (1, 1)
    # The reader was shown the file and the bill as the books hold it.
    assert "Waiting at the border" in str(script.requests) and "2026-118" in str(script.requests)

    bill = (await client.get(f"/invoices/{bill_id}")).json()
    assert bill["about"] == READ["about"] and bill["read_kind"] == "invoice"
    assert bill["lines_balance"] is True and bill["returns"] is False
    lines = (await client.get("/invoice-lines", params={"invoice_id": str(bill_id)})).json()
    assert [(line["charge"], line["plates"], line["days"]) for line in lines] == [
        ("freight", ["34AB1234"], None),
        ("waiting", ["34AB1234"], 2),
        # A kind not offered is "other".
        ("other", ["06XY99"], None),
    ]
    async with open_session() as db:
        plates = await db.scalars(
            select(Fact.normalised).where(Fact.item_id == bill_id, Fact.kind == "plate")
        )
        assert sorted(plates.all()) == ["06XY99", "34AB1234"]
        # The reading keeps the model that made it.
        made = await db.scalar(select(Invoice.lines_made_by).where(Invoice.id == bill_id))
        assert made is not None and made["prompt"]

    # The books read again keep what the reader read in the file.
    await books.store_records(
        company_id, "books", [], [dataclasses.replace(record, description="Freight")]
    )
    async with open_session() as db:
        plates = await db.scalars(
            select(Fact.normalised).where(Fact.item_id == bill_id, Fact.kind == "plate")
        )
        assert sorted(plates.all()) == ["06XY99", "34AB1234"]

    # Read once.
    reader()
    assert (await books.read_pending_bills()).read == 0


@pytest.mark.integration
async def test_no_bill_is_read_while_the_budget_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "bills_read_per_day", 0)
    assert (await books.read_pending_bills()).read == 0
