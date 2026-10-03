import httpx
import pytest
from sqlalchemy import text

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.playground import service as playground
from app.packs.logistics import sandbox
from app.platform.db import open_session
from app.tests.conftest import guest_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


async def test_a_sandbox_opens_with_a_job_one_truck_of_which_can_be_paid(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    # The guest's click only queued the seeding.
    async with open_session() as db:
        queued = await db.scalar(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'playground.seed' "
                "AND args->>'company_id' = :id"
            ),
            {"id": str(company_id)},
        )
    assert queued == 1
    assert (await client.get("/projects")).json()["items"] == []

    await playground.seed(company_id)

    [project] = (await client.get("/projects")).json()["items"]
    assert project["number"] == sandbox.SALE and project["status"] == "active"
    bills = {bill["number"]: bill for bill in (await client.get("/invoices")).json()["items"]}
    assert set(bills) == {sandbox.SALE, sandbox.DELIVERED_BILL, sandbox.MOVING_BILL}
    assert bills[sandbox.SALE]["direction"] == "receivable"
    assert bills[sandbox.SALE]["remaining_amount"] == "0.00"
    assert bills[sandbox.DELIVERED_BILL]["direction"] == "payable"
    assert bills[sandbox.DELIVERED_BILL]["party_id"] is not None

    trucks = {
        truck["truck_plate"]: truck
        for truck in (await client.get("/shipments", params={"project_id": project["id"]})).json()
    }
    assert trucks[sandbox.PLATE_DELIVERED]["status"] == "delivered"
    assert trucks[sandbox.PLATE_MOVING]["status"] == "on_road"
    links = (await client.get("/project-links", params={"project_id": project["id"]})).json()
    assert {link["status"] for link in links["items"]} == {"confirmed"}
    # The delivered truck has its CMR as proof; the one on the road has none.
    [proof] = [link for link in links["items"] if link["proof_of_delivery"]]
    assert proof["item_kind"] == "document"
    assert proof["shipment_id"] == trucks[sandbox.PLATE_DELIVERED]["id"]
    on_road = [
        link for link in links["items"] if link["shipment_id"] == trucks[sandbox.PLATE_MOVING]["id"]
    ]
    assert [link["item_kind"] for link in on_road] == ["invoice"]

    # Seeding again changes nothing.
    await playground.seed(company_id)
    assert len((await client.get("/projects")).json()["items"]) == 1
    assert len((await client.get("/invoices")).json()["items"]) == 3


async def test_only_a_sandbox_is_seeded(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    async with open_session() as db:
        pilot = await companies.create_company(db, name="Pilot", kind=CompanyKind.PILOT)
        await db.commit()

    await playground.seed(pilot.id)

    async with open_session() as db:
        held = await db.scalar(
            text("SELECT count(*) FROM invoices WHERE company_id = :id"), {"id": pilot.id}
        )
    assert held == 0
