"""A freight job's checklist: what each truck and the job still lack, read from confirmed papers,
the owner's proof marks and the books, with the owner's marks over it."""

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord
from app.modules.documents import service as documents
from app.modules.playground import service as playground
from app.modules.projects import service as projects
from app.modules.projects.models import LinkMethod
from app.packs.logistics import sandbox
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.linking.test_carried_files import proposed
from app.tests.linking.test_placing import (
    Script,
    arrive,
    mail,
    model,  # noqa: F401  (the fixture)
    owner_project,
)
from app.tests.logistics.test_filing import CMR, filed, trucks_of

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]


async def states(client: httpx.AsyncClient, project_id: str, **params: Any) -> dict[Any, str]:
    response = await client.get("/checklist-items", params={"project_id": project_id, **params})
    assert response.status_code == 200, response.text
    return {(item["shipment_id"], item["key"]): item["state"] for item in response.json()}


async def the_sandbox(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    company_id = await guest_client(client)
    await playground.seed(company_id)
    await documents.read_pending()
    [project] = (await client.get("/projects")).json()["items"]
    trucks = {truck["truck_plate"]: truck["id"] for truck in await trucks_of(client, project["id"])}
    return project["id"], trucks


async def test_the_sandbox_has_one_truck_proven_and_one_on_the_road(
    client: httpx.AsyncClient,
) -> None:
    project_id, trucks = await the_sandbox(client)
    delivered, moving = trucks[sandbox.PLATE_DELIVERED], trucks[sandbox.PLATE_MOVING]

    items = (await client.get("/checklist-items", params={"project_id": project_id})).json()
    found = await states(client, project_id)

    # The job's own items first, then each truck's.
    assert [item["shipment_id"] for item in items[:4]] == [None] * 4
    assert found[(None, "client_order")] == "not_needed"
    assert found[(None, "client_invoice")] == "done"
    assert found[(None, "client_paid")] == "done"
    assert found[(None, "subcontractor_paid")] == "waiting"
    assert found[(delivered, "cmr")] == "done"
    assert found[(delivered, "proof_of_delivery")] == "done"
    assert found[(delivered, "subcontractor_invoice")] == "done"
    assert found[(moving, "loading_photo")] == "not_needed"
    assert found[(moving, "cmr")] == "missing"
    assert found[(moving, "proof_of_delivery")] == "missing"
    assert found[(moving, "subcontractor_invoice")] == "done"
    missing = await states(client, project_id, state="missing")
    assert set(missing) == {(moving, "cmr"), (moving, "proof_of_delivery")}

    # A CMR on the truck that the owner has not marked as proof waits for that tap.
    links = (await client.get("/project-links", params={"project_id": project_id})).json()
    [proof] = [link for link in links["items"] if link["proof_of_delivery"]]
    await client.patch(f"/project-links/{proof['id']}", json={"proof_of_delivery": False})
    assert (await states(client, project_id))[(delivered, "proof_of_delivery")] == "waiting"


async def test_the_owners_marks_ask_waive_or_fill_an_item_but_never_a_proof(
    client: httpx.AsyncClient,
) -> None:
    project_id, trucks = await the_sandbox(client)
    moving = trucks[sandbox.PLATE_MOVING]

    async def mark(key: str, value: str, shipment_id: str | None = None) -> httpx.Response:
        body = {"project_id": project_id, "shipment_id": shipment_id, "key": key, "mark": value}
        return await client.put("/checklist-marks", json=body)

    waived = await mark("cmr", "not_needed", moving)
    assert waived.status_code == 200, waived.text
    assert (await mark("loading_photo", "needed", moving)).status_code == 200
    ordered = await mark("client_order", "needed")
    assert ordered.status_code == 200, ordered.text
    # The job's own item has no truck: a second mark on it replaces the first all the same.
    assert (await mark("client_order", "done")).json()["id"] == ordered.json()["id"]
    again = await mark("cmr", "needed", moving)
    assert again.json()["id"] == waived.json()["id"]
    assert (await mark("cmr", "not_needed", moving)).status_code == 200

    found = await states(client, project_id, shipment_id=moving)
    assert found[(moving, "cmr")] == "not_needed"
    assert found[(moving, "loading_photo")] == "missing"
    assert (await states(client, project_id))[(None, "client_order")] == "done"

    assert (await mark("proof_of_delivery", "done", moving)).status_code == 400
    assert (await mark("cmr", "not_needed")).status_code == 400
    assert (await mark("client_order", "done", moving)).status_code == 400
    assert (await mark("cmr", "not_needed", str(uuid.uuid4()))).status_code == 400
    assert (await mark("no_such_item", "done")).status_code == 400

    removed = await client.delete(f"/checklist-marks/{waived.json()['id']}")
    assert removed.status_code == 204
    assert (await states(client, project_id))[(moving, "cmr")] == "missing"


async def test_a_merge_carries_the_jobs_marks_and_keeps_a_marked_truck(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, source = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    await filed(company_id)
    [truck] = await trucks_of(client, source)
    await arrive(company_id, 9, mail("<other@x>", subject="Other"), category=None)
    target = str((await owner_project(company_id, "<other@x>", name="Other")).id)
    await client.patch(f"/projects/{source}", json={"status": "active"})
    marked = await client.put(
        "/checklist-marks",
        json={"project_id": source, "shipment_id": truck["id"], "key": "cmr", "mark": "done"},
    )
    assert marked.status_code == 200, marked.text

    assert (
        await client.post(f"/projects/{source}/merge", json={"into": target})
    ).status_code == 409

    await client.delete(f"/checklist-marks/{marked.json()['id']}")
    await client.put(
        "/checklist-marks", json={"project_id": source, "key": "client_order", "mark": "needed"}
    )
    merged = await client.post(f"/projects/{source}/merge", json={"into": target})

    assert merged.status_code == 200, merged.text
    assert (await states(client, target))[(None, "client_order")] == "missing"


async def test_a_bill_a_key_names_counts_and_one_a_plate_suggests_waits(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<one@x>", subject="One"), category=None)
    project = await owner_project(company_id, "<one@x>", name="One")
    sale = InvoiceRecord(
        source_id="s-1",
        direction=InvoiceDirection.RECEIVABLE,
        kind=InvoiceKind.SALES,
        raw={},
        number="S-1",
        issue_date=date(2026, 9, 12),
        total_amount=Decimal("100.00"),
        remaining_amount=Decimal("40.00"),
    )
    carrier = InvoiceRecord(
        source_id="p-1",
        direction=InvoiceDirection.PAYABLE,
        kind=InvoiceKind.PURCHASE,
        raw={},
        number="P-1",
        issue_date=date(2026, 9, 12),
        remaining_amount=Decimal("0.00"),
    )
    await books.store_records(company_id, "books", [], [sale, carrier])
    bills = {bill["number"]: bill["id"] for bill in (await client.get("/invoices")).json()["items"]}
    async with open_session() as db:
        for number, method, confidence in (
            ("S-1", LinkMethod.KEY, 0.9),
            ("P-1", LinkMethod.SCORED, 0.7),
        ):
            await projects.link_item(
                db,
                company_id=company_id,
                item_kind=ItemKind.INVOICE,
                item_id=uuid.UUID(bills[number]),
                project_id=project.id,
                method=method,
                confidence=confidence,
            )
        await db.commit()

    found = await states(client, str(project.id))

    assert found[(None, "client_invoice")] == "done"
    assert found[(None, "client_paid")] == "waiting"
    assert found[(None, "subcontractor_paid")] == "waiting"
