"""A project's file kept by code (ADR-0021): trucks proposed from its own papers, its items placed
on the one truck their plates name, and bills placed by plate within the job's days. Code never
confirms a truck and never marks a proof."""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.sources import InvoiceRecord
from app.modules.documents import service as documents
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource
from app.packs.logistics import filing
from app.packs.logistics import repository as logistics_repository
from app.packs.logistics import service as logistics
from app.packs.logistics.models import ShipmentState
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.documents import pdfs
from app.tests.linking.test_carried_files import proposed
from app.tests.linking.test_placing import (
    Script,
    arrive,
    mail,
    model,  # noqa: F401  (the fixture)
    owner_project,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("storage_bucket")]

CMR = pdfs.pdf(
    pdfs.text("CMR consignment note. Truck: AB 123 CD Trailer: XY 987 ZW. Goods received.")
)
FLEET = ", ".join(f"Truck: FL {n} AA" for n in range(101, 107))


async def filed(company_id: uuid.UUID) -> filing.Filed:
    async with open_session() as db:
        result = await filing.file_company(db, company_id)
        await db.commit()
        return result


async def trucks_of(client: httpx.AsyncClient, project_id: str) -> list[dict[str, Any]]:
    trucks: list[dict[str, Any]] = (
        await client.get("/shipments", params={"project_id": project_id})
    ).json()
    return trucks


async def test_a_truck_paper_proposes_its_truck_and_is_placed_on_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, project_id = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    # One mention in one mail proposes nothing; a fleet list proposes nothing however often.
    for uid, body in ((2, "Truck: QQ 555 RR is on its way"), (3, FLEET), (4, FLEET)):
        await arrive(
            company_id, uid, mail(f"<m{uid}@x>", body=body, references="<order@x>"), category=None
        )

    result = await filed(company_id)

    assert result.trucks == 1
    [truck] = await trucks_of(client, project_id)
    assert truck["state"] == "proposed" and truck["evidence"]
    assert {truck["truck_plate"], truck["trailer_plate"]} == {"AB 123 CD", "XY 987 ZW"}
    assert truck["field_sources"]["truck_plate"] == "extraction"
    [cmr] = (
        await client.get(
            "/project-links", params={"project_id": project_id, "item_kind": "document"}
        )
    ).json()["items"]
    assert cmr["shipment_id"] == truck["id"] and cmr["proof_of_delivery"] is False
    # Filed again, nothing new.
    again = await filed(company_id)
    assert (again.trucks, again.placed) == (0, 0)


async def test_a_rejected_truck_loses_codes_papers_and_stays_out(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, project_id = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    await filed(company_id)
    [truck] = await trucks_of(client, project_id)

    rejected = await client.patch(f"/shipments/{truck['id']}", json={"state": "rejected"})

    assert rejected.status_code == 200, rejected.text
    links = (await client.get("/project-links", params={"project_id": project_id})).json()["items"]
    assert {link["shipment_id"] for link in links} == {None}
    assert (await filed(company_id)).trucks == 0
    assert [t["state"] for t in await trucks_of(client, project_id)] == ["rejected"]


def bill(source_id: str, issued: date, description: str, **fields: object) -> InvoiceRecord:
    return InvoiceRecord(
        source_id=source_id,
        direction=InvoiceDirection.PAYABLE,
        kind=InvoiceKind.PURCHASE,
        raw={},
        number=source_id.upper(),
        issue_date=issued,
        description=description,
        **fields,  # type: ignore[arg-type]
    )


async def a_job(client: httpx.AsyncClient, company_id: uuid.UUID, key: str, plate: str) -> str:
    """A project the owner made, with its days and a confirmed truck."""
    await arrive(company_id, hash(key) % 10_000, mail(key, subject=key), category=None)
    project = await owner_project(company_id, key, name=key)
    days = {"starts_on": "2026-09-01", "ends_on": "2026-09-10"}
    assert (await client.patch(f"/projects/{project.id}", json=days)).status_code == 200
    made = await client.post(
        "/shipments", json={"project_id": str(project.id), "truck_plate": plate}
    )
    assert made.json()["state"] == "confirmed"
    return str(project.id)


async def test_a_bill_is_placed_by_plate_within_the_jobs_days(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    project_id = await a_job(client, company_id, "<one@x>", "AB 123 CD")
    await books.store_records(
        company_id,
        "books",
        [],
        [
            bill("p-in", date(2026, 10, 20), "Freight, truck: AB 123 CD"),
            # Past the job's last day plus sixty, and before its first less ten.
            bill("p-late", date(2026, 11, 20), "Freight, truck: AB 123 CD"),
            bill("p-early", date(2026, 8, 20), "Freight, truck: AB 123 CD"),
            bill("p-void", date(2026, 9, 5), "Freight, truck: AB 123 CD", cancelled=True),
        ],
    )

    result = await filed(company_id)

    assert result.bills == 1
    links = (await client.get("/project-links", params={"item_kind": "invoice"})).json()["items"]
    [link] = links
    assert (link["project_id"], link["method"], link["confidence"]) == (project_id, "scored", 0.7)
    assert link["status"] == "suggested"


async def test_a_plate_two_jobs_share_places_no_bill(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await a_job(client, company_id, "<one@x>", "AB 123 CD")
    await a_job(client, company_id, "<two@x>", "AB 123 CD")
    await books.store_records(
        company_id, "books", [], [bill("p-1", date(2026, 9, 12), "Truck: AB 123 CD")]
    )

    assert (await filed(company_id)).bills == 0


async def test_only_the_owners_proof_counts_and_a_bill_pays_its_plates_truck(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    project_id = await a_job(client, company_id, "<one@x>", "AB 123 CD")
    [truck] = await trucks_of(client, project_id)
    await books.store_records(
        company_id, "books", [], [bill("p-1", date(2026, 9, 12), "Truck: AB 123 CD")]
    )
    await filed(company_id)
    [bill_link] = (await client.get("/project-links", params={"item_kind": "invoice"})).json()[
        "items"
    ]
    upload = await client.post("/documents", params={"filename": "pod.pdf"}, content=b"%PDF pod")
    pod = (
        await client.post(
            "/project-links",
            json={
                "item_kind": "document",
                "item_id": upload.json()["document"]["id"],
                "project_id": project_id,
            },
        )
    ).json()
    async with open_session() as db:
        # A suggestion is no bill paid, and a paper not marked as proof is no proof.
        assert await logistics.trucks_billed(db, company_id, uuid.UUID(bill_link["item_id"])) == []
        assert await logistics.proofs(db, company_id, [uuid.UUID(project_id)]) == {}

    await client.patch(f"/project-links/{bill_link['id']}", json={"status": "confirmed"})
    marked = await client.patch(
        f"/project-links/{pod['id']}",
        json={"shipment_id": truck["id"], "proof_of_delivery": True},
    )

    assert marked.status_code == 200, marked.text
    async with open_session() as db:
        billed = await logistics.trucks_billed(db, company_id, uuid.UUID(bill_link["item_id"]))
        proofs = await logistics.proofs(db, company_id, [uuid.UUID(project_id)])
    assert billed == [uuid.UUID(truck["id"])]
    assert proofs == {uuid.UUID(truck["id"]): [uuid.UUID(pod["id"])]}


async def test_a_merge_drops_proposed_trucks_and_keeps_rejected_plates_out(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, source = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    await filed(company_id)
    [truck] = await trucks_of(client, source)
    await client.patch(f"/shipments/{truck['id']}", json={"state": "rejected"})
    await arrive(company_id, 9, mail("<other@x>", subject="Other"), category=None)
    target = str((await owner_project(company_id, "<other@x>", name="Other")).id)
    await client.patch(f"/projects/{source}", json={"status": "active"})

    merged = await client.post(f"/projects/{source}/merge", json={"into": target})

    assert merged.status_code == 200, merged.text
    [kept] = await trucks_of(client, target)
    assert kept["state"] == "rejected" and kept["id"] != truck["id"]
    # The CMR came with the merge, and its plates are not proposed again.
    assert (await filed(company_id)).trucks == 0


async def test_a_plate_paired_with_a_known_truck_is_its_trailer(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, project_id = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    made = await client.post(
        "/shipments", json={"project_id": project_id, "truck_plate": "AB123CD"}
    )
    assert made.status_code == 201, made.text

    result = await filed(company_id)

    assert result.trucks == 0
    [truck] = await trucks_of(client, project_id)
    assert (truck["truck_plate"], truck["trailer_plate"]) == ("AB123CD", "XY 987 ZW")
    assert truck["field_sources"]["trailer_plate"] == "extraction"
    [cmr] = (
        await client.get(
            "/project-links", params={"project_id": project_id, "item_kind": "document"}
        )
    ).json()["items"]
    assert cmr["shipment_id"] == truck["id"]


async def test_codes_truck_never_overwrites_an_owners_answer_given_meanwhile(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, project_id = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    named = await client.post(
        "/shipments", json={"project_id": project_id, "truck_plate": "AB 123 CD"}
    )
    other = await client.post(
        "/shipments", json={"project_id": project_id, "truck_plate": "QQ 1 RR"}
    )
    async with open_session() as db:
        links = await projects.links_of_project(db, company_id, uuid.UUID(project_id))
        plates = await filing._plates(db, company_id, links)
        trucks = await logistics_repository.list_shipments(db, company_id, [uuid.UUID(project_id)])
        [cmr] = [link for link in links if link.item_kind is ItemKind.DOCUMENT]
        # The owner answers between code's read and its write.
        answered = await client.patch(
            f"/project-links/{cmr.id}",
            json={"shipment_id": other.json()["id"], "proof_of_delivery": True},
        )
        assert answered.status_code == 200, answered.text

        placed = await filing._place_on_trucks(db, company_id, links, plates, trucks)
        await db.commit()

    assert placed == 0 and named.status_code == 201
    [link] = (
        await client.get(
            "/project-links", params={"project_id": project_id, "item_kind": "document"}
        )
    ).json()["items"]
    assert (link["shipment_id"], link["method"]) == (other.json()["id"], "owner")


async def test_a_merge_keeps_a_truck_the_owner_wrote_on(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, source = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    await filed(company_id)
    [truck] = await trucks_of(client, source)
    await client.patch(f"/shipments/{truck['id']}", json={"driver_name": "A. Driver"})
    await arrive(company_id, 9, mail("<other@x>", subject="Other"), category=None)
    target = str((await owner_project(company_id, "<other@x>", name="Other")).id)
    await client.patch(f"/projects/{source}", json={"status": "active"})

    merged = await client.post(f"/projects/{source}/merge", json={"into": target})

    assert merged.status_code == 409, merged.text
    assert [t["driver_name"] for t in await trucks_of(client, source)] == ["A. Driver"]


async def test_a_merged_rejection_rejects_the_same_truck_proposed_on_the_target(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, source = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    await filed(company_id)
    [truck] = await trucks_of(client, source)
    await client.patch(f"/shipments/{truck['id']}", json={"state": "rejected"})
    await arrive(company_id, 9, mail("<other@x>", subject="Other"), category=None)
    target = (await owner_project(company_id, "<other@x>", name="Other")).id
    async with open_session() as db:
        same = await logistics.add_shipment(db, company_id=company_id, project_id=target)
        same.state = ShipmentState.PROPOSED
        await logistics.fill_shipment(
            db, same, {"truck_plate": "AB 123 CD"}, source=FieldSource.EXTRACTION
        )
        await db.commit()
    await client.patch(f"/projects/{source}", json={"status": "active"})

    merged = await client.post(f"/projects/{source}/merge", json={"into": str(target)})

    assert merged.status_code == 200, merged.text
    kept = await trucks_of(client, str(target))
    assert sorted((t["truck_plate"], t["state"]) for t in kept) == [
        ("AB 123 CD", "rejected"),
        ("AB 123 CD", "rejected"),
    ]


async def test_a_bill_pays_the_truck_only_its_e_invoice_names(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    project_id = await a_job(client, company_id, "<one@x>", "AB 123 CD")
    [truck] = await trucks_of(client, project_id)
    e_invoice = InvoiceRecord(
        source_id="e-1",
        direction=InvoiceDirection.PAYABLE,
        kind=InvoiceKind.INCOMING_E_INVOICE,
        raw={},
        number="E-1",
        issue_date=date(2026, 9, 12),
        description="Truck: AB 123 CD",
        same_as_source_id="purchase:1",
    )
    await books.store_records(
        company_id, "books", [], [bill("purchase:1", date(2026, 9, 12), "Freight"), e_invoice]
    )
    await filed(company_id)
    [link] = (await client.get("/project-links", params={"item_kind": "invoice"})).json()["items"]
    await client.patch(f"/project-links/{link['id']}", json={"status": "confirmed"})

    async with open_session() as db:
        billed = await logistics.trucks_billed(db, company_id, uuid.UUID(link["item_id"]))

    assert billed == [uuid.UUID(truck["id"])]
