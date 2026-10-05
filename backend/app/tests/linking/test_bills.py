"""A bill no project holds is suggested to the one project that holds an item sharing a key
number with it, placed once however many lists of the books hold it."""

import uuid
from datetime import date

import httpx
import pytest

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord
from app.modules.facts import service as facts
from app.modules.linking import service
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierSource, PartyKind
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.linking.test_placing import arrive, mail, owner_project

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

ORDER = "Order no 4500123 for the Munich load."


def bill(source_id: str, kind: InvoiceKind = InvoiceKind.SALES, **fields: object) -> InvoiceRecord:
    direction = (
        InvoiceDirection.RECEIVABLE if kind is InvoiceKind.SALES else InvoiceDirection.PAYABLE
    )
    values: dict[str, object] = {
        "source_id": source_id,
        "direction": direction,
        "kind": kind,
        "raw": {},
        "issue_date": date(2026, 9, 30),
    }
    return InvoiceRecord(**(values | fields))  # type: ignore[arg-type]


async def placed(company_id: uuid.UUID) -> service.BillsPlaced:
    async with open_session() as db:
        result = await service.place_invoices(db, company_id)
        await db.commit()
        return result


async def bill_links(client: httpx.AsyncClient) -> dict[str, dict[str, object]]:
    """Each bill's links by its number."""
    numbers = {
        invoice["id"]: invoice["number"]
        for invoice in (await client.get("/invoices")).json()["items"]
    }
    links = (await client.get("/project-links", params={"item_kind": "invoice"})).json()["items"]
    return {str(numbers[link["item_id"]]): link for link in links}


async def test_a_bill_is_suggested_where_its_order_number_is(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>", body=ORDER), category=None)
    project = await owner_project(company_id, "<order@x>")
    await books.store_records(
        company_id,
        "books",
        [],
        [
            bill("sales:1", number="S-1", description="Freight, order 4500123"),
            # Cancelled, or the company's own expense: never a job's.
            bill("sales:2", number="S-2", description="Order 4500123", cancelled=True),
            bill("expense:3", InvoiceKind.EXPENSE, number="E-3", description="Order 4500123"),
            bill("sales:4", number="S-4", description="Freight to Lyon"),
        ],
    )

    result = await placed(company_id)

    assert (result.linked, result.several, result.none) == (1, 0, 1)
    links = await bill_links(client)
    assert set(links) == {"S-1"}
    assert links["S-1"]["project_id"] == str(project.id)
    assert (links["S-1"]["method"], links["S-1"]["status"]) == ("key", "suggested")
    assert links["S-1"]["confidence"] == 0.9 and links["S-1"]["evidence"]
    # Placed once: the next run finds nothing new to place.
    again = await placed(company_id)
    assert (again.linked, again.none) == (0, 1)


async def test_a_bill_two_projects_could_hold_waits_and_a_rejected_one_stays_out(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<one@x>", body=ORDER), category=None)
    await arrive(company_id, 2, mail("<two@x>", subject="Again", body=ORDER), category=None)
    await owner_project(company_id, "<one@x>", name="One")
    await owner_project(company_id, "<two@x>", name="Two")
    await books.store_records(
        company_id, "books", [], [bill("sales:1", number="S-1", description=ORDER)]
    )

    result = await placed(company_id)

    assert (result.linked, result.several) == (0, 1)
    assert await bill_links(client) == {}


async def test_an_owners_rejection_is_not_suggested_again(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>", body=ORDER), category=None)
    await owner_project(company_id, "<order@x>")
    await books.store_records(
        company_id, "books", [], [bill("sales:1", number="S-1", description=ORDER)]
    )
    await placed(company_id)
    link = (await bill_links(client))["S-1"]

    rejected = await client.patch(f"/project-links/{link['id']}", json={"status": "rejected"})

    assert rejected.status_code == 200, rejected.text
    assert (await placed(company_id)).linked == 0
    assert (await bill_links(client))["S-1"]["status"] == "rejected"


async def test_a_booked_e_invoice_is_placed_as_its_purchase(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await arrive(
        company_id,
        1,
        mail("<bill@x>", body="Invoice no INV-2026-118 for the Munich load"),
        category=None,
    )
    project = await owner_project(company_id, "<bill@x>")
    await books.store_records(
        company_id,
        "books",
        [],
        [
            bill("purchase:9", InvoiceKind.PURCHASE, number="P-9"),
            # Only the e-invoice carries the number the mail names.
            bill(
                "incoming:5",
                InvoiceKind.INCOMING_E_INVOICE,
                number="INV-2026-118",
                same_as_source_id="purchase:9",
            ),
        ],
    )

    result = await placed(company_id)

    assert result.linked == 1
    links = await bill_links(client)
    assert set(links) == {"P-9"}
    assert links["P-9"]["project_id"] == str(project.id)
    invoices = {each["number"]: each for each in (await client.get("/invoices")).json()["items"]}
    assert invoices["INV-2026-118"]["same_as_id"] == invoices["P-9"]["id"]


async def test_an_e_invoice_held_before_it_was_booked_moves_to_its_purchase(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<bill@x>", body="Invoice no INV-2026-118"), category=None)
    await owner_project(company_id, "<bill@x>")
    e_invoice = bill("incoming:5", InvoiceKind.INCOMING_E_INVOICE, number="INV-2026-118")
    await books.store_records(company_id, "books", [], [e_invoice])
    assert (await placed(company_id)).linked == 1
    assert set(await bill_links(client)) == {"INV-2026-118"}

    # Booked: the purchase comes, and the e-invoice names it.
    booked = bill(
        "incoming:5",
        InvoiceKind.INCOMING_E_INVOICE,
        number="INV-2026-118",
        same_as_source_id="purchase:9",
    )
    await books.store_records(
        company_id, "books", [], [bill("purchase:9", InvoiceKind.PURCHASE, number="P-9"), booked]
    )
    await placed(company_id)

    assert set(await bill_links(client)) == {"P-9"}


async def test_a_corrected_bill_loses_its_old_numbers_and_a_placeholder_is_no_key(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>", body=ORDER), category=None)
    await owner_project(company_id, "<order@x>")
    await books.store_records(
        company_id, "books", [], [bill("sales:1", number="N/A", description=ORDER)]
    )
    # The books correct it before the place job runs.
    await books.store_records(
        company_id, "books", [], [bill("sales:1", number="N/A", description="Freight to Lyon")]
    )

    assert (await placed(company_id)).linked == 0
    async with open_session() as db:
        [invoice] = await books.placeable_invoices(db, company_id)
        kinds = {
            fact.kind
            for fact in await facts.facts_of(
                db, company_id=company_id, item_kind=ItemKind.INVOICE, item_id=invoice.id
            )
        }
    assert kinds == set()


async def test_bills_are_placed_for_a_company_with_no_conversation(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    project = (await client.post("/projects", json={"name": "Munich"})).json()
    await books.store_records(
        company_id,
        "books",
        [],
        [
            bill("sales:1", number="S-1", description=ORDER),
            bill("purchase:2", InvoiceKind.PURCHASE, number="P-2", description=ORDER),
        ],
    )
    [sale] = [
        each for each in (await client.get("/invoices")).json()["items"] if each["number"] == "S-1"
    ]
    made = await client.post(
        "/project-links",
        json={"item_kind": "invoice", "item_id": sale["id"], "project_id": project["id"]},
    )
    assert made.status_code == 201, made.text

    await service.place_pending()

    links = await bill_links(client)
    assert links["P-2"]["project_id"] == project["id"] and links["P-2"]["status"] == "suggested"


async def client_is(client: httpx.AsyncClient, company_id: uuid.UUID, project_id: uuid.UUID) -> str:
    """Names the project's client, as the owner does: the id of the project's party row."""
    async with open_session() as db:
        acme = await parties.create_party(
            db,
            company_id=company_id,
            name="ACME FREIGHT SERVICES LTD",
            kind=PartyKind.ORGANISATION,
            source=IdentifierSource.OWNER,
        )
        await db.commit()
    body = {"project_id": str(project_id), "party_id": str(acme.id), "role": "client"}
    made = await client.post("/project-parties", json=body)
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


async def test_a_sale_to_another_client_is_counted_not_placed(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>", body=ORDER), category=None)
    project = await owner_project(company_id, "<order@x>")
    await client_is(client, company_id, project.id)
    await books.store_records(
        company_id,
        "books",
        [],
        [
            # The client's name abbreviated, as the books often write it.
            bill("sales:1", number="S-1", description=ORDER, party_name="ACME FREIGHT SERV."),
            bill("sales:2", number="S-2", description=ORDER, party_name="Stone GmbH"),
            # A job buys from more firms than its carriers: a purchase is never held against them.
            bill("buy:3", InvoiceKind.PURCHASE, number="P-3", description=ORDER, party_name="Port"),
        ],
    )

    result = await placed(company_id)

    assert (result.linked, result.contradicted) == (2, 1)
    assert set(await bill_links(client)) == {"S-1", "P-3"}


async def test_rechecking_removes_codes_sales_placed_with_another_client(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>", body=ORDER), category=None)
    project = await owner_project(company_id, "<order@x>")
    await books.store_records(
        company_id,
        "books",
        [],
        [
            bill(f"sales:{n}", number=f"S-{n}", description=ORDER, party_name="Stone GmbH")
            for n in (1, 2)
        ],
    )
    await placed(company_id)
    answered = (await bill_links(client))["S-2"]
    await client.patch(f"/project-links/{answered['id']}", json={"status": "confirmed"})
    # The client named after the bills were placed.
    await client_is(client, company_id, project.id)

    async with open_session() as db:
        removed = await service.recheck_invoices(db, company_id)
        await db.commit()

    assert removed == 1
    assert set(await bill_links(client)) == {"S-2"}
