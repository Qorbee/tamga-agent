import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import packs
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierSource, PartyKind
from app.modules.projects import service
from app.modules.projects.models import (
    FieldSource,
    LinkMethod,
    LinkStatus,
    ProjectStatus,
    ProposalStatus,
)
from app.packs.logistics import service as shipments
from app.platform.db import open_session
from app.platform.errors import Conflict
from app.platform.items import ItemKind
from app.tests.conftest import guest_client

pytestmark = pytest.mark.integration


async def pilot(db: AsyncSession) -> uuid.UUID:
    return (await companies.create_company(db, name="Acme Lojistik", kind=CompanyKind.PILOT)).id


async def test_drawn_numbers_follow_the_format_and_skip_a_typed_one(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    response = await client.put("/project-numbering", json={"format": "A{seq:3}", "next_seq": 41})
    assert response.json()["next_number"] == "A041"

    typed = await client.post("/projects", json={"name": "Typed", "number": "a 042"})
    first = await client.post("/projects", json={"name": "Istanbul → Munich"})
    second = await client.post("/projects", json={"name": "Izmir → Lyon"})

    assert typed.status_code == 201
    assert [first.json()["number"], second.json()["number"]] == ["A041", "A043"]
    assert first.json()["status"] == "draft"
    assert first.json()["field_sources"] == {"name": "owner", "number": "suggestion"}


async def test_an_edited_number_stays_an_alias_no_other_project_can_take(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    project = (await client.post("/projects", json={"name": "One", "number": "P-1"})).json()

    renamed = await client.patch(f"/projects/{project['id']}", json={"number": "P-1B"})
    taken = await client.post("/projects", json={"name": "Two", "number": "p-1"})
    back = await client.patch(f"/projects/{project['id']}", json={"number": "P-1"})

    assert renamed.json()["number"] == "P-1B" and renamed.json()["aliases"] == ["P-1"]
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "number_taken"
    assert back.status_code == 200  # its own earlier number
    found = await client.get("/projects", params={"q": "p-1b"})
    assert [p["id"] for p in found.json()["items"]] == [project["id"]]


async def test_a_project_of_another_company_is_not_found(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    theirs = (await client.post("/projects", json={"name": "Theirs"})).json()
    await guest_client(client)  # a second sandbox: the session now acts for it

    assert (await client.get(f"/projects/{theirs['id']}")).status_code == 404
    assert (await client.get("/projects")).json()["total"] == 0


async def test_status_moves_only_as_the_owner_decides(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    project = (await client.post("/projects", json={"name": "One"})).json()
    path = f"/projects/{project['id']}"

    assert (await client.patch(path, json={"status": "closed"})).status_code == 400
    assert (await client.patch(path, json={"status": "active"})).json()["status"] == "active"
    assert (await client.patch(path, json={"status": "closed"})).json()["status"] == "closed"


async def test_an_extraction_after_the_owner_edit_waits_as_a_proposal(db: AsyncSession) -> None:
    # ADR-0010, Confirmation.
    company_id = await pilot(db)
    project = await service.new_project(
        db,
        company_id=company_id,
        name="Owner's name",
        status=ProjectStatus.DRAFT,
        source=FieldSource.OWNER,
    )

    [proposal] = await service.propose_project_fields(
        db,
        company_id=company_id,
        project_id=project.id,
        values={"name": "Read from a CMR"},
        source=FieldSource.EXTRACTION,
        evidence=[],
    )
    [newer] = await service.propose_project_fields(
        db,
        company_id=company_id,
        project_id=project.id,
        values={"name": "Read again"},
        source=FieldSource.EXTRACTION,
        evidence=[],
    )

    assert project.name == "Owner's name"
    assert proposal.id == newer.id and newer.value == "Read again"
    assert newer.status is ProposalStatus.OPEN


async def test_an_accepted_proposal_becomes_the_owner_s_value(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    project = (await client.post("/projects", json={"name": "Owner's"})).json()
    from app.platform.db import open_session

    async with open_session() as db:
        await service.propose_project_fields(
            db,
            company_id=uuid.UUID((await client.get("/me")).json()["company"]["id"]),
            project_id=uuid.UUID(project["id"]),
            values={"name": "Suggested"},
            source=FieldSource.SUGGESTION,
            evidence=[],
        )
        await db.commit()

    [proposal] = (await client.get("/field-proposals", params={"status": "open"})).json()["items"]
    decided = await client.patch(f"/field-proposals/{proposal['id']}", json={"status": "accepted"})
    again = await client.patch(f"/field-proposals/{proposal['id']}", json={"status": "dismissed"})

    assert decided.json()["status"] == "accepted"
    assert again.status_code == 409
    after = (await client.get(f"/projects/{project['id']}")).json()
    assert after["name"] == "Suggested" and after["field_sources"]["name"] == "owner"


async def test_a_suggestion_fills_an_empty_field_but_not_an_extracted_one(
    db: AsyncSession,
) -> None:
    company_id = await pilot(db)
    project = await service.new_project(
        db,
        company_id=company_id,
        name="Proposed by the linker",
        status=ProjectStatus.PROPOSED,
        source=FieldSource.EXTRACTION,
    )
    proposals = await service.propose_project_fields(
        db,
        company_id=company_id,
        project_id=project.id,
        values={"name": "A guess"},
        source=FieldSource.SUGGESTION,
        evidence=[],
    )
    assert project.name == "Proposed by the linker" and len(proposals) == 1


async def test_a_proof_of_delivery_confirms_one_truck_only(db: AsyncSession) -> None:
    packs.register_all()
    company_id = await pilot(db)
    project = await service.new_project(
        db,
        company_id=company_id,
        name="Two trucks",
        status=ProjectStatus.DRAFT,
        source=FieldSource.OWNER,
    )
    other = await service.new_project(
        db,
        company_id=company_id,
        name="Another job",
        status=ProjectStatus.DRAFT,
        source=FieldSource.OWNER,
    )
    truck_a = await shipments.add_shipment(db, company_id=company_id, project_id=project.id)
    truck_b = await shipments.add_shipment(db, company_id=company_id, project_id=other.id)
    cmr = uuid.uuid4()

    first = await service.link_item(
        db,
        company_id=company_id,
        item_kind=ItemKind.DOCUMENT,
        item_id=cmr,
        project_id=project.id,
        shipment_id=truck_a.id,
        method=LinkMethod.KEY,
        confidence=0.97,
        proof_of_delivery=True,
        confirm=True,
    )
    assert first.status is LinkStatus.CONFIRMED
    assert (
        project.status is ProjectStatus.ACTIVE
        or (await service.get_projects(db, company_id, [project.id]))[project.id].status
        is ProjectStatus.ACTIVE
    )

    with pytest.raises(Conflict):
        await service.link_item(
            db,
            company_id=company_id,
            item_kind=ItemKind.DOCUMENT,
            item_id=cmr,
            project_id=other.id,
            shipment_id=truck_b.id,
            method=LinkMethod.OWNER,
            confidence=1.0,
            proof_of_delivery=True,
            confirm=True,
        )


async def test_a_link_to_a_truck_of_another_project_is_refused(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    one = (await client.post("/projects", json={"name": "One"})).json()
    two = (await client.post("/projects", json={"name": "Two"})).json()
    truck = (await client.post("/shipments", json={"project_id": two["id"]})).json()
    upload = await client.post("/documents", params={"filename": "cmr.pdf"}, content=b"%PDF a CMR")
    document_id = upload.json()["document"]["id"]

    wrong = await client.post(
        "/project-links",
        json={
            "item_kind": "document",
            "item_id": document_id,
            "project_id": one["id"],
            "shipment_id": truck["id"],
        },
    )
    right = await client.post(
        "/project-links",
        json={"item_kind": "document", "item_id": document_id, "project_id": one["id"]},
    )
    missing = await client.post(
        "/project-links",
        json={"item_kind": "message", "item_id": str(uuid.uuid4()), "project_id": one["id"]},
    )

    assert wrong.status_code == 400 and wrong.json()["error"]["field"] == "shipment_id"
    assert right.json()["status"] == "confirmed" and right.json()["method"] == "owner"
    assert missing.status_code == 404
    assert (await client.get(f"/projects/{one['id']}")).json()["status"] == "active"


async def test_a_rejected_link_stays_rejected_when_suggested_again(db: AsyncSession) -> None:
    company_id = await pilot(db)
    project = await service.new_project(
        db, company_id=company_id, name="One", status=ProjectStatus.DRAFT, source=FieldSource.OWNER
    )
    mail = uuid.uuid4()
    link = await service.link_item(
        db,
        company_id=company_id,
        item_kind=ItemKind.MESSAGE,
        item_id=mail,
        project_id=project.id,
        method=LinkMethod.SCORED,
        confidence=0.4,
    )
    link.status = LinkStatus.REJECTED
    link.decided_at = link.created_at
    await db.flush()

    again = await service.link_item(
        db,
        company_id=company_id,
        item_kind=ItemKind.MESSAGE,
        item_id=mail,
        project_id=project.id,
        method=LinkMethod.AGENT,
        confidence=0.9,
        confirm=True,
    )
    assert again.id == link.id and again.status is LinkStatus.REJECTED


@pytest.mark.integration
async def test_the_owner_sets_and_clears_a_project_s_days(client: httpx.AsyncClient) -> None:
    await guest_client(client)
    project = (await client.post("/projects", json={"name": "Munich"})).json()
    assert (project["starts_on"], project["ends_on"]) == (None, None)

    days = {"starts_on": "2026-08-10", "ends_on": "2026-08-14"}
    set_ = (await client.patch(f"/projects/{project['id']}", json=days)).json()
    assert (set_["starts_on"], set_["ends_on"]) == ("2026-08-10", "2026-08-14")
    assert set_["field_sources"]["starts_on"] == "owner"

    # A day sent as null is cleared; one left out stays.
    cleared = (await client.patch(f"/projects/{project['id']}", json={"ends_on": None})).json()
    assert (cleared["starts_on"], cleared["ends_on"]) == ("2026-08-10", None)

    backwards = await client.patch(f"/projects/{project['id']}", json={"ends_on": "2026-08-01"})
    assert backwards.status_code == 400 and backwards.json()["error"]["field"] == "ends_on"
    # Projects with a first day come first when sorted by it; those without follow.
    await client.post("/projects", json={"name": "No day yet"})
    listed = (await client.get("/projects", params={"sort": "starts_on"})).json()["items"]
    assert [item["name"] for item in listed] == ["Munich", "No day yet"]


@pytest.mark.integration
async def test_no_writer_leaves_a_project_ending_before_it_starts(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    days = {"name": "Munich", "starts_on": "2026-08-10", "ends_on": "2026-08-14"}
    project = (await client.post("/projects", json={"name": "Munich"})).json()
    await client.patch(f"/projects/{project['id']}", json=days)
    from app.platform.db import open_session

    company_id = uuid.UUID((await client.get("/me")).json()["company"]["id"])
    async with open_session() as db:
        # The owner set the last day, so mail that says otherwise can only propose.
        await service.propose_project_fields(
            db,
            company_id=company_id,
            project_id=uuid.UUID(project["id"]),
            values={"ends_on": "2026-08-01"},
            source=FieldSource.EXTRACTION,
            evidence=[],
        )
        await db.commit()
    [proposal] = (await client.get("/field-proposals", params={"status": "open"})).json()["items"]
    accepted = await client.patch(f"/field-proposals/{proposal['id']}", json={"status": "accepted"})
    assert accepted.status_code == 400 and accepted.json()["error"]["field"] == "ends_on"

    # With no last day set, a backwards one read from mail is not written at all.
    await client.patch(f"/projects/{project['id']}", json={"ends_on": None})
    async with open_session() as db:
        await service.propose_project_fields(
            db,
            company_id=company_id,
            project_id=uuid.UUID(project["id"]),
            values={"ends_on": "2026-08-01"},
            source=FieldSource.SUGGESTION,
            evidence=[],
        )
        await db.commit()
    after = (await client.get(f"/projects/{project['id']}")).json()
    assert (after["starts_on"], after["ends_on"]) == ("2026-08-10", None)


async def test_a_merge_keeps_the_link_that_says_more_and_refuses_what_cannot_follow(
    client: httpx.AsyncClient,
) -> None:
    await guest_client(client)
    one, two, trucks = [
        (await client.post("/projects", json={"name": name})).json()
        for name in ("One", "Two", "With trucks")
    ]

    async def paper(name: str, *projects: dict[str, str]) -> dict[str, str]:
        """A file, tied by the owner to each of these projects: project id → link id."""
        upload = await client.post("/documents", params={"filename": name}, content=name.encode())
        tied = {}
        for project in projects:
            link = await client.post(
                "/project-links",
                json={
                    "item_kind": "document",
                    "item_id": upload.json()["document"]["id"],
                    "project_id": project["id"],
                },
            )
            tied[project["id"]] = link.json()["id"]
        return tied

    both = await paper("cmr.pdf", one, two)
    await paper("order.pdf", one)
    refused = await paper("other.pdf", one)
    # Of the paper both hold, the one that stays said no; the other no was said in the one that
    # goes, of a paper the one that stays never saw.
    for link_id in (both[two["id"]], refused[one["id"]]):
        rejected = await client.patch(f"/project-links/{link_id}", json={"status": "rejected"})
        assert rejected.status_code == 200
    assert (await client.post("/shipments", json={"project_id": trucks["id"]})).status_code == 201

    held = await client.post(f"/projects/{trucks['id']}/merge", json={"into": two["id"]})
    merged = await client.post(f"/projects/{one['id']}/merge", json={"into": two["id"]})

    # A project's trucks are tied to it: the owner merges the other way.
    assert held.status_code == 409
    assert (await client.get(f"/projects/{trucks['id']}")).status_code == 200
    assert merged.status_code == 200, merged.text
    kept = merged.json()
    assert (kept["name"], kept["number"], kept["aliases"]) == (
        "Two",
        two["number"],
        [one["number"]],
    )
    assert kept["confirmed_links"] == 2
    links = (await client.get("/project-links", params={"project_id": two["id"]})).json()
    # The yes outlives the no for the paper both held, and the other no came along as it was.
    assert sorted(link["status"] for link in links["items"]) == [
        "confirmed",
        "confirmed",
        "rejected",
    ]
    back = await client.post(f"/projects/{trucks['id']}/merge", json={"into": trucks["id"]})
    assert back.status_code == 400 and back.json()["error"]["field"] == "into"


async def test_a_merge_keeps_a_party_s_price_and_refuses_two_that_differ(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    async with open_session() as db:
        hauler, client_firm = [
            await parties.create_party(
                db,
                company_id=company_id,
                name=name,
                kind=PartyKind.ORGANISATION,
                source=IdentifierSource.OWNER,
            )
            for name in ("Hauler Ltd", "Stone GmbH")
        ]
        await db.commit()
    one, two = [
        (await client.post("/projects", json={"name": name})).json() for name in ("One", "Two")
    ]

    async def terms(
        project: dict[str, str], party_id: uuid.UUID, role: str, amount: str | None
    ) -> None:
        body = {"project_id": project["id"], "party_id": str(party_id), "role": role}
        priced = {"amount": amount, "currency": "EUR"} if amount else {}
        assert (await client.post("/project-parties", json=body | priced)).status_code == 201

    # The carrier's price is known in the one that goes only; the client's differs in the two.
    await terms(one, hauler.id, "subcontractor", "1800.00")
    await terms(two, hauler.id, "subcontractor", None)
    await terms(one, client_firm.id, "client", "2400.00")
    await terms(two, client_firm.id, "client", "2500.00")

    refused = await client.post(f"/projects/{one['id']}/merge", json={"into": two["id"]})

    assert refused.status_code == 409 and refused.json()["error"]["field"] == "into"
    assert (await client.get(f"/projects/{one['id']}")).status_code == 200
    kept = (await client.get("/project-parties", params={"project_id": two["id"]})).json()
    assert sorted(row["amount"] or "" for row in kept) == ["", "2500.00"]
    # The owner settles the price, and the merge keeps the one the other knew.
    mine = (await client.get("/project-parties", params={"project_id": one["id"]})).json()
    client_row = next(row for row in mine if row["role"] == "client")
    assert (await client.delete(f"/project-parties/{client_row['id']}")).status_code == 204
    assert (
        await client.post(f"/projects/{one['id']}/merge", json={"into": two["id"]})
    ).status_code == 200
    kept = (await client.get("/project-parties", params={"project_id": two["id"]})).json()
    assert sorted(row["amount"] for row in kept) == ["1800.00", "2500.00"]


async def test_a_merge_confirms_no_truck_the_owner_did_not(client: httpx.AsyncClient) -> None:
    company_id = await guest_client(client)
    one, two = [
        (await client.post("/projects", json={"name": name})).json() for name in ("One", "Two")
    ]
    truck = (await client.post("/shipments", json={"project_id": two["id"]})).json()
    upload = await client.post("/documents", params={"filename": "pod.pdf"}, content=b"%PDF pod")
    document_id = upload.json()["document"]["id"]
    # In the project that stays, the paper is only suggested as that truck's proof of delivery.
    async with open_session() as db:
        await service.link_item(
            db,
            company_id=company_id,
            item_kind=ItemKind.DOCUMENT,
            item_id=uuid.UUID(document_id),
            project_id=uuid.UUID(two["id"]),
            method=LinkMethod.SCORED,
            confidence=0.5,
            shipment_id=uuid.UUID(truck["id"]),
            proof_of_delivery=True,
        )
        await db.commit()
    tied = await client.post(
        "/project-links",
        json={"item_kind": "document", "item_id": document_id, "project_id": one["id"]},
    )
    assert tied.json()["status"] == "confirmed"

    merged = await client.post(f"/projects/{one['id']}/merge", json={"into": two["id"]})

    assert merged.status_code == 200, merged.text
    [link] = (await client.get("/project-links", params={"project_id": two["id"]})).json()["items"]
    # The owner confirmed the paper for the job, never for the truck: it is no truck's proof.
    assert (link["status"], link["shipment_id"], link["proof_of_delivery"]) == (
        "confirmed",
        None,
        False,
    )
