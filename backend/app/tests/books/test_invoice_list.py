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
