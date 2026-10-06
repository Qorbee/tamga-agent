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
from app.modules.books.sources import InvoiceRecord, PartyCard
from app.modules.conversations import service as conversations
from app.modules.documents import service as documents
from app.modules.facts import repository as facts_repository
from app.modules.parties import service as parties
from app.modules.parties.models import PartyKind
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource
from app.modules.projects.schemas import ProjectJob
from app.packs.logistics import filing
from app.packs.logistics import repository as logistics_repository
from app.packs.logistics import service as logistics
from app.packs.logistics.facts import TRAILER_ROLE
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
    # Each plate says where it was read.
    assert {(s["field"], s["item_kind"], s["label"]) for s in truck["sources"]} == {
        ("truck_plate", "document", "CMR 4471.pdf"),
        ("trailer_plate", "document", "CMR 4471.pdf"),
    }
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


# The carrier the jobs below are given to, as the books know it.
HAULER = PartyCard("hauler", "HAULER TRANSPORT SERVICES LTD", PartyKind.ORGANISATION)


def bill(source_id: str, issued: date, description: str, **fields: object) -> InvoiceRecord:
    """The carrier's bill, unless ``fields`` say otherwise."""
    values: dict[str, object] = {
        "source_id": source_id,
        "direction": InvoiceDirection.PAYABLE,
        "kind": InvoiceKind.PURCHASE,
        "raw": {},
        "number": source_id.upper(),
        "issue_date": issued,
        "description": description,
        "party_source_id": HAULER.source_id,
        "party_name": HAULER.name,
    }
    return InvoiceRecord(**(values | fields))  # type: ignore[arg-type]


def firm(name: str) -> dict[str, object]:
    """A bill's firm by its name alone, as a record that names no card has it."""
    return {"party_source_id": None, "party_name": name}


async def a_job(client: httpx.AsyncClient, company_id: uuid.UUID, key: str, plate: str) -> str:
    """A project the owner made, with its days, a confirmed truck, and the carrier."""
    await arrive(company_id, hash(key) % 10_000, mail(key, subject=key), category=None)
    project = await owner_project(company_id, key, name=key)
    days = {"starts_on": "2026-09-01", "ends_on": "2026-09-10"}
    assert (await client.patch(f"/projects/{project.id}", json=days)).status_code == 200
    await books.store_records(company_id, "books", [HAULER], [])
    async with open_session() as db:
        hauler = await parties.get_synced_party(db, company_id, "books", HAULER.source_id)
    assert hauler is not None
    carrier = {"project_id": str(project.id), "party_id": str(hauler.id), "role": "subcontractor"}
    assert (await client.post("/project-parties", json=carrier)).status_code == 201
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


def note(words: str) -> bytes:
    return pdfs.pdf(pdfs.text(f"CMR consignment note. {words} Goods received."))


async def with_notes(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
    notes: dict[str, str],
) -> tuple[uuid.UUID, str]:
    """A proposed project whose first mail carried a consignment note for each of ``notes``."""
    attach = {name: note(words) for name, words in notes.items()}
    placement = await proposed(client, model, attach=attach)
    await documents.read_pending()
    return uuid.UUID(placement["company_id"]), placement["project_id"]


def plates_of(trucks: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    return sorted((truck["truck_plate"], truck["trailer_plate"]) for truck in trucks)


async def test_a_notes_two_plates_are_its_truck_and_trailer_the_truck_written_first(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(
        client,
        model,
        {
            "CMR 1.pdf": "Plate: AB 123 CD, plate: XY 987 ZW",
            # A trailer named alone is no truck.
            "CMR 2.pdf": "Trailer: TR 555 LR",
        },
    )

    await filed(company_id)

    assert plates_of(await trucks_of(client, project_id)) == [("AB 123 CD", "XY 987 ZW")]


async def test_no_more_trucks_than_the_job_has_the_best_shown_first(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id, project_id = await with_notes(
        client,
        model,
        {
            "CMR 1.pdf": "Plate: AA 111 AA",
            "CMR 2.pdf": "Truck: AA 111 AA arrived.",
            "CMR 3.pdf": "Plate: BB 222 BB",
            "CMR 4.pdf": "Plate: CC 333 CC",
        },
    )
    real = projects.jobs_of

    async def two_trucks(*args: Any) -> dict[uuid.UUID, Any]:
        jobs = await real(*args)
        return {uuid.UUID(project_id): ProjectJob(trucks=2)} | {
            key: job for key, job in jobs.items() if key != uuid.UUID(project_id)
        }

    monkeypatch.setattr(projects, "jobs_of", two_trucks)

    result = await filed(company_id)

    assert result.trucks == 2
    # The plate two notes name comes first; of the others, one.
    proposed_plates = [truck for truck, _ in plates_of(await trucks_of(client, project_id))]
    assert "AA 111 AA" in proposed_plates and len(proposed_plates) == 2


async def test_a_misread_is_one_truck_and_two_plates_on_one_paper_are_two(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(
        client,
        model,
        {
            "CMR 1.pdf": "Plate: AB 123 CD",
            # The same plate with two neighbours swapped, as a reading slips.
            "CMR 2.pdf": "Plate: AB 213 CD",
            "CMR 3.pdf": "Plate: AB 123 CD",
        },
    )

    await filed(company_id)

    assert plates_of(await trucks_of(client, project_id)) == [("AB 123 CD", None)]
    assert filing._misread("AB123CD", "AB213CD") and filing._misread("06KLM345", "06KML345")
    assert filing._misread("34AB108", "34AB10B")  # 8 read as B
    assert not filing._misread("06XYZ900", "06XYZ901")  # two trucks of one fleet
    assert not filing._misread("34AB123", "06AB123")


async def test_a_note_outside_the_jobs_days_names_no_truck(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(client, model, {"CMR 1.pdf": "Plate: AB 123 CD"})
    # The note came on 3 August; this job loads in late September.
    days = {"starts_on": "2026-09-20", "ends_on": "2026-09-25"}
    assert (await client.patch(f"/projects/{project_id}", json=days)).status_code == 200

    assert (await filed(company_id)).trucks == 0


async def test_refiling_drops_only_the_trucks_the_owner_had_no_hand_in(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(client, model, {"CMR 1.pdf": "Plate: AB 123 CD"})
    await filed(company_id)
    async with open_session() as db:
        # Trucks an earlier rule proposed from a mail's plates.
        for plate in ("MM 999 MM", "OW 111 NR"):
            truck = await logistics.add_shipment(
                db, company_id=company_id, project_id=uuid.UUID(project_id)
            )
            truck.state = ShipmentState.PROPOSED
            await logistics.fill_shipment(
                db, truck, {"truck_plate": plate}, source=FieldSource.EXTRACTION
            )
        await db.commit()
    [written] = [t for t in await trucks_of(client, project_id) if t["truck_plate"] == "OW 111 NR"]
    await client.patch(f"/shipments/{written['id']}", json={"driver_name": "A. Driver"})

    async with open_session() as db:
        refiled = await filing.refile_company(db, company_id)
        await db.commit()

    [project] = [each for each in refiled if str(each.project_id) == project_id]
    assert (project.before, project.dropped, project.proposed, project.after) == (3, 2, 1, 2)
    assert plates_of(await trucks_of(client, project_id)) == [
        ("AB 123 CD", None),
        ("OW 111 NR", None),
    ]


async def test_a_trailer_label_read_again_from_old_papers_and_mail(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, _ = await with_notes(client, model, {"CMR 1.pdf": "Trailer: XY 987 ZW"})
    await arrive(company_id, 2, mail("<m2@x>", body="Trailer: QQ 555 RR"), category=None)
    kinds = frozenset({TRAILER_ROLE})
    async with open_session() as db:
        # Read before code knew a plate's role.
        for kind in (ItemKind.DOCUMENT, ItemKind.MESSAGE):
            roles = await facts_repository.of_kind(db, company_id, TRAILER_ROLE, kind, 10)
            await facts_repository.forget_ids(db, company_id, [fact.id for fact in roles])
        await db.commit()

    async with open_session() as db:
        files, after = await documents.reread_facts(db, company_id, kinds, after=None)
        assert (await documents.reread_facts(db, company_id, kinds, after=after))[1] is None
        mails, _ = await conversations.reread_facts(db, company_id, kinds, after=None)
        await db.commit()
        [paper] = await facts_repository.of_kind(db, company_id, TRAILER_ROLE, ItemKind.DOCUMENT, 9)
        [said] = await facts_repository.of_kind(db, company_id, TRAILER_ROLE, ItemKind.MESSAGE, 9)
    assert (files, mails) == (1, 1)
    assert (paper.normalised, said.normalised) == ("XY987ZW", "QQ555RR")


async def test_a_misread_of_a_kept_truck_is_that_truck(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(client, model, {"CMR 1.pdf": "Plate: AB 123 C0"})
    made = await client.post(
        "/shipments", json={"project_id": project_id, "truck_plate": "AB123CD"}
    )
    assert made.status_code == 201, made.text

    assert (await filed(company_id)).trucks == 0


async def test_a_fleet_lists_labels_say_nothing_of_a_jobs_trucks(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    fleet = "Trailer: AB 123 CD, " + ", ".join(f"Truck: FL {n} AA" for n in range(101, 105))
    company_id, project_id = await with_notes(
        client, model, {"CMR 1.pdf": "Plate: AB 123 CD", "Fleet.pdf": fleet}
    )

    await filed(company_id)

    assert plates_of(await trucks_of(client, project_id)) == [("AB 123 CD", None)]


async def test_a_jobs_last_day_bounds_its_papers_without_a_first(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(client, model, {"CMR 1.pdf": "Plate: AB 123 CD"})
    # The note came on 3 August; this job ended in June.
    assert (
        await client.patch(f"/projects/{project_id}", json={"ends_on": "2026-06-30"})
    ).is_success

    assert (await filed(company_id)).trucks == 0


async def test_a_paper_that_misread_a_kept_truck_hitches_its_trailer_and_is_placed_on_it(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    company_id, project_id = await with_notes(
        client, model, {"CMR 1.pdf": "Plate: AB 123 C0, plate: XY 987 ZW"}
    )
    made = await client.post(
        "/shipments", json={"project_id": project_id, "truck_plate": "AB123CD"}
    )
    truck = made.json()

    assert (await filed(company_id)).trucks == 0

    assert plates_of(await trucks_of(client, project_id)) == [("AB123CD", "XY 987 ZW")]
    [cmr] = (
        await client.get(
            "/project-links", params={"project_id": project_id, "item_kind": "document"}
        )
    ).json()["items"]
    assert cmr["shipment_id"] == truck["id"]


async def test_a_plate_places_only_a_bill_of_the_jobs_own_firm(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    await a_job(client, company_id, "<one@x>", "AB 123 CD")
    await books.store_records(
        company_id,
        "books",
        [],
        [
            # The same truck carried another firm's load for a client Tamga has no project of.
            bill("p-other", date(2026, 9, 12), "Truck: AB 123 CD", **firm("OTHER CARRIER LTD")),
            bill(
                "s-1",
                date(2026, 9, 12),
                "Freight, truck: AB 123 CD",
                kind=InvoiceKind.SALES,
                direction=InvoiceDirection.RECEIVABLE,
                **firm("STONE GMBH"),
            ),
            # A shortened name of the job's carrier.
            bill("p-own", date(2026, 9, 12), "Truck: AB 123 CD", **firm("HAULER TRANS. SERV.")),
        ],
    )

    assert (await filed(company_id)).bills == 1
    [link] = (await client.get("/project-links", params={"item_kind": "invoice"})).json()["items"]
    [own] = [i for i in (await client.get("/invoices")).json()["items"] if i["number"] == "P-OWN"]
    # No client is known for the sale: the plate alone does not place it.
    assert link["item_id"] == own["id"]


async def test_rechecking_removes_codes_bills_by_plate_once_the_firm_differs(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    project_id = await a_job(client, company_id, "<one@x>", "AB 123 CD")
    await books.store_records(
        company_id,
        "books",
        [],
        [bill(f"p-{n}", date(2026, 9, 12), "Truck: AB 123 CD") for n in (1, 2)],
    )
    assert (await filed(company_id)).bills == 2
    links = (await client.get("/project-links", params={"item_kind": "invoice"})).json()["items"]
    await client.patch(f"/project-links/{links[0]['id']}", json={"status": "confirmed"})
    # The owner takes the carrier off the job: the plate alone is left.
    [carrier] = (await client.get("/project-parties", params={"project_id": project_id})).json()
    assert (await client.delete(f"/project-parties/{carrier['id']}")).status_code == 204

    async with open_session() as db:
        removed = await filing.recheck_bills_by_plate(db, company_id)
        await db.commit()

    assert removed == 1
    kept = (await client.get("/project-links", params={"item_kind": "invoice"})).json()["items"]
    assert [link["id"] for link in kept] == [links[0]["id"]]


async def test_a_trailer_split_off_and_hitched_back_keeps_where_it_was_read(
    client: httpx.AsyncClient,
    model: Callable[..., Script],  # noqa: F811
) -> None:
    placement = await proposed(client, model, attach={"CMR 4471.pdf": CMR})
    company_id, project_id = uuid.UUID(placement["company_id"]), placement["project_id"]
    await documents.read_pending()
    await filed(company_id)
    [truck] = await trucks_of(client, project_id)

    split = await client.post(f"/shipments/{truck['id']}/split")

    assert split.status_code == 201
    lone = split.json()
    assert (lone["truck_plate"], lone["trailer_plate"]) == ("XY 987 ZW", None)
    assert [(s["field"], s["label"]) for s in lone["sources"]] == [("truck_plate", "CMR 4471.pdf")]
    assert plates_of(await trucks_of(client, project_id)) == [
        ("AB 123 CD", None),
        ("XY 987 ZW", None),
    ]
    # Split again from a board that still shows the trailer: nothing to split.
    assert (await client.post(f"/shipments/{truck['id']}/split")).status_code == 409

    # Shown a plate the row no longer has, the step is refused.
    stale = await client.post(
        f"/shipments/{truck['id']}/hitch",
        json={"from_shipment_id": lone["id"], "plate": "XY 987 ZZ"},
    )
    assert stale.status_code == 409
    hitched = await client.post(
        f"/shipments/{truck['id']}/hitch",
        json={"from_shipment_id": lone["id"], "plate": "XY 987 ZW"},
    )

    assert hitched.status_code == 200
    assert (hitched.json()["truck_plate"], hitched.json()["trailer_plate"]) == (
        "AB 123 CD",
        "XY 987 ZW",
    )
    assert {(s["field"], s["label"]) for s in hitched.json()["sources"]} == {
        ("truck_plate", "CMR 4471.pdf"),
        ("trailer_plate", "CMR 4471.pdf"),
    }
    states = {t["id"]: t["state"] for t in await trucks_of(client, project_id)}
    assert states[lone["id"]] == "rejected"
    # The same step from a stale tab: the truck has its trailer now, and the plate is no truck.
    again = await client.post(
        f"/shipments/{truck['id']}/hitch",
        json={"from_shipment_id": lone["id"], "plate": "XY 987 ZW"},
    )
    assert again.status_code == 409
    # Swapped as they stand.
    swapped = (await client.post(f"/shipments/{truck['id']}/swap")).json()
    assert (swapped["truck_plate"], swapped["trailer_plate"]) == ("XY 987 ZW", "AB 123 CD")
