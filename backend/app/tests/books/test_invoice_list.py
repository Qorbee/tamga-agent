"""The books' invoices listed as a page of them asks: by kind, what is still to be paid or what
is settled, and in the order asked for."""

import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.modules.books.models import Invoice, InvoiceDirection, InvoiceKind
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


def bill(
    company_id: uuid.UUID,
    number: str,
    kind: InvoiceKind,
    *,
    total: str,
    left: str,
    due: date,
    cancelled: bool = False,
) -> Invoice:
    return Invoice(
        company_id=company_id,
        source="ledger",
        source_id=number,
        direction=InvoiceDirection.RECEIVABLE
        if kind is InvoiceKind.SALES
        else InvoiceDirection.PAYABLE,
        kind=kind,
        number=number,
        issue_date=date(2026, 9, 1),
        due_date=due,
        currency="EUR",
        total_amount=Decimal(total),
        remaining_amount=Decimal(left),
        cancelled=cancelled,
        raw={},
    )


async def test_invoices_are_listed_by_kind_by_what_is_left_and_in_the_order_asked(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        db.add_all(
            [
                bill(
                    company_id,
                    "S-1",
                    InvoiceKind.SALES,
                    total="900",
                    left="0",
                    due=date(2026, 9, 10),
                ),
                bill(
                    company_id,
                    "S-2",
                    InvoiceKind.SALES,
                    total="500",
                    left="500",
                    due=date(2026, 9, 30),
                ),
                bill(
                    company_id,
                    "P-1",
                    InvoiceKind.PURCHASE,
                    total="300",
                    left="300",
                    due=date(2026, 9, 20),
                ),
                bill(
                    company_id,
                    "P-2",
                    InvoiceKind.PURCHASE,
                    total="700",
                    left="700",
                    due=date(2026, 9, 5),
                ),
                bill(
                    company_id,
                    "E-1",
                    InvoiceKind.EXPENSE,
                    total="40",
                    left="40",
                    due=date(2026, 9, 1),
                ),
                # Cancelled, so nothing is owed on it whatever the books say is left.
                bill(
                    company_id,
                    "P-3",
                    InvoiceKind.PURCHASE,
                    total="100",
                    left="100",
                    due=date(2026, 9, 1),
                    cancelled=True,
                ),
            ]
        )
        await db.commit()

    async def numbers(**params: str | bool | list[str]) -> list[str]:
        page = (await client.get("/invoices", params=params)).json()
        return [invoice["number"] for invoice in page["items"]]

    assert await numbers(kind=["purchase", "expense"], outstanding=True, sort="due_date") == [
        "E-1",
        "P-2",
        "P-1",
    ]
    assert await numbers(kind="purchase", outstanding=True, sort="-total_amount") == ["P-2", "P-1"]
    assert await numbers(kind="sales", outstanding=False) == ["S-1"]
    assert await numbers(kind="purchase", outstanding=False) == ["P-3"]
    assert await numbers(direction="receivable", sort="-remaining_amount") == ["S-2", "S-1"]
    assert (await client.get("/invoices", params={"sort": "amount"})).status_code == 422
    # Overdue on a day: still owed, and due before it.
    assert await numbers(
        kind=["purchase", "expense"], outstanding=True, due_before="2026-09-10", sort="due_date"
    ) == ["E-1", "P-2"]

    # What the same lists add up to: a cancelled bill counts in the total, and neither in what is
    # left nor in what was paid.
    totals = (await client.get("/invoice-totals", params={"kind": "purchase"})).json()
    assert totals == [
        {
            "currency": "EUR",
            "count": 3,
            "total_amount": "1100.00",
            "remaining_amount": "1000.00",
            "paid_amount": "0.00",
        }
    ]
    open_sales = (
        await client.get("/invoice-totals", params={"kind": "sales", "outstanding": True})
    ).json()
    assert [(t["count"], t["remaining_amount"]) for t in open_sales] == [(1, "500.00")]


async def test_totals_are_never_summed_across_currencies(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        lira = bill(
            company_id, "S-1", InvoiceKind.SALES, total="1000", left="400", due=date(2026, 9, 1)
        )
        lira.currency = "TRY"
        db.add_all(
            [
                lira,
                bill(
                    company_id,
                    "S-2",
                    InvoiceKind.SALES,
                    total="300",
                    left="300",
                    due=date(2026, 9, 1),
                ),
                bill(
                    company_id,
                    "S-3",
                    InvoiceKind.SALES,
                    total="200",
                    left="0",
                    due=date(2026, 9, 1),
                ),
            ]
        )
        await db.commit()

    totals = (await client.get("/invoice-totals", params={"kind": "sales"})).json()
    assert totals == [
        {
            "currency": "EUR",
            "count": 2,
            "total_amount": "500.00",
            "remaining_amount": "300.00",
            "paid_amount": "200.00",
        },
        {
            "currency": "TRY",
            "count": 1,
            "total_amount": "1000.00",
            "remaining_amount": "400.00",
            "paid_amount": "600.00",
        },
    ]

    # A bill whose remainder the books do not give: what is left is unknown, not understated.
    async with open_session() as db:
        unknown = bill(
            company_id, "S-4", InvoiceKind.SALES, total="50", left="0", due=date(2026, 9, 1)
        )
        unknown.remaining_amount = None
        db.add(unknown)
        await db.commit()
    totals = (await client.get("/invoice-totals", params={"kind": "sales"})).json()
    assert totals[0] == {
        "currency": "EUR",
        "count": 3,
        "total_amount": "550.00",
        "remaining_amount": None,
        "paid_amount": None,
    }
    # Another company's invoices are in none of them.
    await guest_client(client)
    assert (await client.get("/invoice-totals")).json() == []
