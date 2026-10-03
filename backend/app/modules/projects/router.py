import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.modules.identity.deps import ActorDep
from app.modules.projects import checklist, service
from app.modules.projects.models import LinkStatus, ProjectStatus, ProposalStatus, ProposalTarget
from app.modules.projects.schemas import (
    ChecklistItemOut,
    ChecklistMarkOut,
    ChecklistMarkSet,
    ChecklistState,
    LinkCreate,
    LinkOut,
    LinkPage,
    LinkUpdate,
    NumberingOut,
    NumberingUpdate,
    ProjectCreate,
    ProjectMerge,
    ProjectOut,
    ProjectPage,
    ProjectPartyCreate,
    ProjectPartyOut,
    ProjectSort,
    ProjectUpdate,
    ProposalDecision,
    ProposalOut,
    ProposalPage,
)
from app.platform.db import SessionDep
from app.platform.items import ItemKind
from app.platform.pagination import PageDep

projects = APIRouter(tags=["projects"])
links = APIRouter(tags=["project_links"])
proposals = APIRouter(tags=["field_proposals"])
checklists = APIRouter(tags=["checklists"])


@projects.get("/projects")
async def list_projects(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    project_status: Annotated[list[ProjectStatus], Query(alias="status")] = [],  # noqa: B006
    q: Annotated[str | None, Query(max_length=100)] = None,
    # True: only projects holding a link that waits for the owner; false: only those holding none.
    suggested: bool | None = None,
    sort: ProjectSort = "-created_at",
) -> ProjectPage:
    return await service.list_projects(
        db, actor, statuses=project_status, q=q, suggested=suggested, sort=sort, page=page
    )


@projects.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, actor: ActorDep, db: SessionDep) -> ProjectOut:
    return await service.create_project(db, actor, name=body.name, number=body.number)


@projects.get("/projects/{project_id}")
async def get_project(project_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ProjectOut:
    return await service.get_project(db, actor, project_id)


@projects.patch("/projects/{project_id}")
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, actor: ActorDep, db: SessionDep
) -> ProjectOut:
    return await service.update_project(
        db,
        actor,
        project_id,
        name=body.name,
        number=body.number,
        dates={
            field: getattr(body, field)
            for field in ("starts_on", "ends_on")
            if field in body.model_fields_set
        },
        status=ProjectStatus(body.status) if body.status else None,
    )


@projects.post("/projects/{project_id}/merge")
async def merge_project(
    project_id: uuid.UUID, body: ProjectMerge, actor: ActorDep, db: SessionDep
) -> ProjectOut:
    """Returns the project that stays."""
    return await service.merge_project(db, actor, project_id, into=body.into)


@projects.get("/project-numbering")
async def get_numbering(actor: ActorDep, db: SessionDep) -> NumberingOut:
    return await service.get_numbering(db, actor)


@projects.put("/project-numbering")
async def set_numbering(body: NumberingUpdate, actor: ActorDep, db: SessionDep) -> NumberingOut:
    return await service.set_numbering(db, actor, number_format=body.format, next_seq=body.next_seq)


@projects.get("/project-parties")
async def list_project_parties(
    actor: ActorDep,
    db: SessionDep,
    project_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=200)],
) -> list[ProjectPartyOut]:
    return await service.list_project_parties(db, actor, project_id)


@projects.post("/project-parties", status_code=status.HTTP_201_CREATED)
async def add_project_party(
    body: ProjectPartyCreate, actor: ActorDep, db: SessionDep
) -> ProjectPartyOut:
    return await service.add_project_party(
        db,
        actor,
        project_id=body.project_id,
        party_id=body.party_id,
        role=body.role,
        layer=body.layer,
        amount=body.amount,
        currency=body.currency,
    )


@projects.delete("/project-parties/{project_party_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_party(
    project_party_id: uuid.UUID, actor: ActorDep, db: SessionDep
) -> None:
    await service.remove_project_party(db, actor, project_party_id)


@links.get("/project-links")
async def list_links(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    project_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
    link_status: Annotated[list[LinkStatus], Query(alias="status")] = [],  # noqa: B006
    item_kind: ItemKind | None = None,
    item_id: uuid.UUID | None = None,
    expand: Annotated[list[Literal["item"]], Query()] = [],  # noqa: B006
) -> LinkPage:
    return await service.list_links(
        db,
        actor,
        project_id=project_id,
        shipment_id=shipment_id,
        statuses=link_status,
        item_kind=item_kind,
        item_id=item_id,
        with_items="item" in expand,
        page=page,
    )


@links.post("/project-links", status_code=status.HTTP_201_CREATED)
async def create_link(body: LinkCreate, actor: ActorDep, db: SessionDep) -> LinkOut:
    return await service.create_link(
        db,
        actor,
        item_kind=ItemKind(body.item_kind),
        item_id=body.item_id,
        project_id=body.project_id,
        shipment_id=body.shipment_id,
        proof_of_delivery=body.proof_of_delivery,
    )


@links.patch("/project-links/{link_id}")
async def update_link(
    link_id: uuid.UUID, body: LinkUpdate, actor: ActorDep, db: SessionDep
) -> LinkOut:
    # Only the fields sent: an omitted shipment_id keeps the truck, an explicit null clears it.
    return await service.update_link(db, actor, link_id, body.model_dump(exclude_unset=True))


@proposals.get("/field-proposals")
async def list_proposals(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    proposal_status: Annotated[list[ProposalStatus], Query(alias="status")] = [],  # noqa: B006
    target: ProposalTarget | None = None,
    target_id: uuid.UUID | None = None,
) -> ProposalPage:
    return await service.list_proposals(
        db, actor, statuses=proposal_status, target=target, target_id=target_id, page=page
    )


@proposals.patch("/field-proposals/{proposal_id}")
async def decide_proposal(
    proposal_id: uuid.UUID, body: ProposalDecision, actor: ActorDep, db: SessionDep
) -> ProposalOut:
    return await service.decide_proposal(db, actor, proposal_id, accept=body.status == "accepted")


@checklists.get("/checklist-items")
async def list_checklist_items(
    actor: ActorDep,
    db: SessionDep,
    project_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=200)],
    shipment_id: uuid.UUID | None = None,
    state: Annotated[list[ChecklistState] | None, Query()] = None,
) -> list[ChecklistItemOut]:
    return await checklist.list_items(
        db, actor, project_id, shipment_id=shipment_id, states=state or ()
    )


@checklists.put("/checklist-marks")
async def set_checklist_mark(
    body: ChecklistMarkSet, actor: ActorDep, db: SessionDep
) -> ChecklistMarkOut:
    return await checklist.set_mark(db, actor, body)


@checklists.delete("/checklist-marks/{mark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_checklist_mark(mark_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> None:
    await checklist.delete_mark(db, actor, mark_id)
