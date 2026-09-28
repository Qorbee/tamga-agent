import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import packs
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.projects import service
from app.modules.projects.models import (
    FieldSource,
    LinkMethod,
    LinkStatus,
    ProjectStatus,
    ProposalStatus,
)
from app.packs.logistics import service as shipments
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
