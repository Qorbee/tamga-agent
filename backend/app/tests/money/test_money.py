"""A company's money as accounts and movements from sources that plug in (ADR-0026): the owner's
own, the books' estimate, a sandbox's banks; the owner's category kept through every sync; totals
in one currency at the reference rate, and nothing guessed."""

import dataclasses
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord, PartyCard
from app.modules.money import service as money
from app.modules.money.models import AccountKind, MovementCategory
from app.modules.money.schemas import AccountRecord, MovementRecord
from app.modules.parties.models import PartyKind
from app.modules.playground import service as playground
from app.packs.logistics import sandbox
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

TODAY = date(2026, 10, 9)


async def movements(client: httpx.AsyncClient, **params: Any) -> list[dict[str, Any]]:
    response = await client.get("/money/movements", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def test_the_owner_keeps_a_cash_box_and_says_what_moved(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    made = await client.post(
        "/money/accounts",
        json={"kind": "cash", "name": "Office safe", "currency": "EUR", "balance": "800.00"},
    )
    assert made.status_code == 201, made.text
    box = made.json()
    assert box["source"] == "typed" and Decimal(box["balance"]) == 800

    moved = await client.post(
        "/money/movements",
        json={
            "account_id": box["id"],
            "booked_on": "2026-10-08",
            "amount": "-200.00",
            "description": "Taken by a partner",
            "category": "partner_withdrawal",
        },
    )
    assert moved.status_code == 201, moved.text
    assert moved.json()["category_by"] == "owner" and moved.json()["currency"] == "EUR"
    counted = await client.patch(f"/money/accounts/{box['id']}", json={"balance": "600.00"})
    assert Decimal(counted.json()["balance"]) == 600


async def test_a_sources_rows_stay_its_own_and_the_owners_category_stays(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    account = AccountRecord(
        source_ref="a1",
        kind=AccountKind.BANK,
        name="Bank",
        currency="EUR",
        iban="NL02ABNA0123456789",
    )
    fee = MovementRecord(
        source_ref="f1", account_ref="a1", booked_on=TODAY, amount=Decimal("-5"), currency="EUR"
    )
    # Two equal fees on one day are two movements.
    twin = MovementRecord(
        source_ref="f2", account_ref="a1", booked_on=TODAY, amount=Decimal("-5"), currency="EUR"
    )
    await money.store(company_id, "bank", [account], [fee, twin])
    [bank] = (await client.get("/money/accounts")).json()["items"]
    assert bank["source"] == "bank" and len(await movements(client)) == 2

    # A source's account changes at the source, never by hand.
    assert (
        await client.patch(f"/money/accounts/{bank['id']}", json={"name": "x"})
    ).status_code == 409
    first = (await movements(client))[0]
    owned = await client.patch(f"/money/movements/{first['id']}", json={"category": "bank_fee"})
    assert owned.json()["category_by"] == "owner"

    # The source says both again, with a category of its own: the owner's stays.
    again = [dataclasses.replace(each, category=MovementCategory.OTHER) for each in (fee, twin)]
    await money.store(company_id, "bank", [account], again)
    assert sorted((m["category"], m["category_by"]) for m in await movements(client)) == [
        ("bank_fee", "owner"),
        ("other", "code"),
    ]
    # Whole, it drops what it no longer gives.
    await money.store(company_id, "bank", [account], again[:1], whole=True)
    assert len(await movements(client)) == 1


async def test_the_books_estimate_what_came_in_and_went_out(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    card = PartyCard(source_id="P-1", name="Acme Haulage", kind=PartyKind.ORGANISATION)

    def bill(ref: str, **more: Any) -> InvoiceRecord:
        return InvoiceRecord(
            source_id=ref,
            raw={},
            number=ref,
            party_source_id="P-1",
            party_name="Acme Haulage",
            issue_date=TODAY - timedelta(days=30),
            due_date=TODAY - timedelta(days=10),
            currency="EUR",
            total_amount=Decimal("1000.00"),
            **more,
        )

    await books.store_records(
        company_id,
        "test",
        [card],
        [
            bill(
                "S-1",
                direction=InvoiceDirection.RECEIVABLE,
                kind=InvoiceKind.SALES,
                remaining_amount=Decimal("0"),
            ),
            bill(
                "P-1",
                direction=InvoiceDirection.PAYABLE,
                kind=InvoiceKind.PURCHASE,
                remaining_amount=Decimal("400.00"),
            ),
            bill(
                "P-2",
                direction=InvoiceDirection.PAYABLE,
                kind=InvoiceKind.PURCHASE,
                remaining_amount=Decimal("1000.00"),
            ),
            bill(
                "P-3",
                direction=InvoiceDirection.PAYABLE,
                kind=InvoiceKind.PURCHASE,
                remaining_amount=Decimal("0"),
                cancelled=True,
            ),
            bill(
                "E-1",
                direction=InvoiceDirection.PAYABLE,
                kind=InvoiceKind.INCOMING_E_INVOICE,
                remaining_amount=Decimal("0"),
            ),
        ],
    )
    await money.sync_company(company_id)

    estimates = {item["description"]: item for item in await movements(client, estimated=True)}
    # Collected in full, paid in part; nothing for an unpaid, a cancelled or an e-invoice record.
    assert set(estimates) == {"S-1", "P-1"}
    assert Decimal(estimates["S-1"]["amount"]) == 1000
    assert estimates["S-1"]["category"] == "client_receipt"
    assert Decimal(estimates["P-1"]["amount"]) == -600 and estimates["P-1"]["category"] is None
    assert estimates["S-1"]["booked_on"] == (TODAY - timedelta(days=10)).isoformat()
    [books_account] = (await client.get("/money/accounts", params={"kind": "books"})).json()[
        "items"
    ]
    assert books_account["currency"] == "EUR" and books_account["balance"] is None

    # A bank's movement naming the invoice replaces its estimate.
    await money.store(
        company_id,
        "bank",
        [AccountRecord(source_ref="a", kind=AccountKind.BANK, name="Bank", currency="EUR")],
        [
            MovementRecord(
                source_ref="m",
                account_ref="a",
                booked_on=TODAY,
                amount=Decimal("1000.00"),
                currency="EUR",
                invoice_id=uuid.UUID(estimates["S-1"]["invoice_id"]),
            )
        ],
    )
    await money.sync_company(company_id)
    assert {item["description"] for item in await movements(client, estimated=True)} == {"P-1"}


async def test_a_sandbox_holds_three_currencies_counted_in_one(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await playground.seed(company_id)
    await money.sync_company(company_id)

    accounts = (await client.get("/money/accounts", params={"kind": "bank"})).json()["items"]
    assert sorted(account["currency"] for account in accounts) == ["EUR", "GBP", "USD"]
    # The client's payment is the bank's, so the books do not count it a second time.
    sale = [item for item in await movements(client) if item["reference"] == sandbox.SALE]
    assert [item["estimated"] for item in sale] == [False]
    # Money about no job is there too, each with its category.
    categories = {item["category"] for item in await movements(client)}
    assert {"salary", "tax", "bank_fee", "partner_withdrawal", "own_transfer"} <= categories

    held = (await client.get("/money/holdings", params={"currency": "USD"})).json()
    assert held["complete"] is True and len(held["items"]) == 3
    by_currency = {item["currency"]: item for item in held["items"]}
    # 18,400 euros at 1.16, 4,100 pounds at 1.34, 6,200 dollars as they are.
    assert Decimal(by_currency["EUR"]["converted"]) == Decimal("21344.00")
    assert by_currency["EUR"]["rate_source"] == "test"
    assert Decimal(by_currency["USD"]["rate"]) == 1
    assert held["total"] == "33038.00"


async def test_an_account_no_rate_converts_is_left_out_and_said(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    for currency, balance in (("EUR", "100.00"), ("CHF", "50.00")):
        await client.post(
            "/money/accounts",
            json={"kind": "bank", "name": currency, "currency": currency, "balance": balance},
        )
    held = (await client.get("/money/holdings", params={"currency": "EUR"})).json()
    assert held["complete"] is False and held["total"] == "100.00"
    [chf] = [item for item in held["items"] if item["currency"] == "CHF"]
    assert chf["converted"] is None and chf["rate"] is None


async def test_money_stays_in_its_company_and_only_the_owner_writes(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    box = (
        await client.post(
            "/money/accounts", json={"kind": "cash", "name": "Safe", "currency": "EUR"}
        )
    ).json()
    other = await guest_client(client)
    assert (await client.get("/money/accounts")).json()["items"] == []
    missing = await client.patch(f"/money/accounts/{box['id']}", json={"name": "Mine"})
    assert missing.status_code == 404
    async with open_session() as db:
        await db.execute(
            text("UPDATE memberships SET role = 'viewer' WHERE company_id = :id"), {"id": other}
        )
        await db.commit()
    refused = await client.post(
        "/money/accounts", json={"kind": "cash", "name": "Safe", "currency": "EUR"}
    )
    assert refused.status_code == 403


async def test_a_mirror_never_replaces_an_estimate_and_a_part_payment_leaves_the_rest(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await books.store_records(
        company_id,
        "test",
        [PartyCard(source_id="P-1", name="Acme Haulage", kind=PartyKind.ORGANISATION)],
        [
            InvoiceRecord(
                source_id="P-9",
                raw={},
                number="P-9",
                direction=InvoiceDirection.PAYABLE,
                kind=InvoiceKind.PURCHASE,
                party_source_id="P-1",
                due_date=TODAY,
                currency="EUR",
                total_amount=Decimal("1000.00"),
                remaining_amount=Decimal("0"),
            )
        ],
    )
    await money.sync_company(company_id)
    [estimate] = await movements(client, estimated=True)
    invoice_id = uuid.UUID(estimate["invoice_id"])
    owned = await client.patch(
        f"/money/movements/{estimate['id']}", json={"category": "subcontractor_payment"}
    )
    assert owned.status_code == 200

    # Test USDC released for the bill is a mirror: the estimate of the real payment stays.
    await money.store(
        company_id,
        "vault",
        [AccountRecord(source_ref="v", kind=AccountKind.VAULT, name="Vault", currency="USDC")],
        [
            MovementRecord(
                source_ref="r",
                account_ref="v",
                booked_on=TODAY,
                amount=Decimal("-0.116"),
                currency="USDC",
                invoice_id=invoice_id,
            )
        ],
    )
    await money.sync_company(company_id)
    assert [Decimal(m["amount"]) for m in await movements(client, estimated=True)] == [-1000]

    # A bank pays 400 of it: 600 stays estimated, and the owner's category moves to the bank's.
    bank = AccountRecord(source_ref="b", kind=AccountKind.BANK, name="Bank", currency="EUR")
    await money.store(
        company_id,
        "bank",
        [bank],
        [
            MovementRecord(
                source_ref="t1",
                account_ref="b",
                booked_on=TODAY,
                amount=Decimal("-400"),
                currency="EUR",
                invoice_id=invoice_id,
            )
        ],
    )
    await money.sync_company(company_id)
    assert [Decimal(m["amount"]) for m in await movements(client, estimated=True)] == [-600]
    [paid] = await movements(client, estimated=False, account_id=(await _bank(client))["id"])
    assert (paid["category"], paid["category_by"]) == ("subcontractor_payment", "owner")


async def _bank(client: httpx.AsyncClient) -> dict[str, Any]:
    items: list[dict[str, Any]] = (
        await client.get("/money/accounts", params={"kind": "bank"})
    ).json()["items"]
    [bank] = items
    return bank


async def test_an_account_read_through_another_source_stays_one(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    iban = "NL91ABNA0417164300"
    typed = await client.post(
        "/money/accounts",
        json={"kind": "bank", "name": "Main", "currency": "EUR", "iban": iban, "balance": "10"},
    )
    assert typed.status_code == 201
    again = await client.post(
        "/money/accounts", json={"kind": "bank", "name": "Again", "currency": "EUR", "iban": iban}
    )
    assert again.status_code == 409
    # A bank source connected later takes the account over: one account, now the bank's.
    await money.store(
        company_id,
        "bank",
        [
            AccountRecord(
                source_ref="acc-1",
                kind=AccountKind.BANK,
                name="Main (bank)",
                currency="EUR",
                iban=iban,
                balance=Decimal("12.00"),
            )
        ],
        [],
    )
    bank = await _bank(client)
    assert bank["id"] == typed.json()["id"] and bank["source"] == "bank"
    assert Decimal(bank["balance"]) == 12
