import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.projects.models import (
    FieldProposal,
    FieldSource,
    LinkStatus,
    Project,
    ProjectLink,
    ProjectNumber,
    ProjectNumbering,
    ProjectParty,
    ProjectStatus,
    ProposalStatus,
    ProposalTarget,
)
from app.platform.items import ItemKind
from app.platform.pagination import PageParams, escape_like


async def add(db: AsyncSession, row: Any) -> None:
    db.add(row)
    await db.flush()


async def get_project(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID, *, for_update: bool = False
) -> Project | None:
    statement = select(Project).where(Project.company_id == company_id, Project.id == project_id)
    if for_update:
        statement = statement.with_for_update()
    project: Project | None = await db.scalar(statement)
    return project


async def get_projects(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Project]:
    rows = await db.scalars(
        select(Project).where(Project.company_id == company_id, Project.id.in_(ids))
    )
    return rows.all()


def _project_filters(
    company_id: uuid.UUID, statuses: Sequence[ProjectStatus], q: str | None
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [Project.company_id == company_id]
    if statuses:
        filters.append(Project.status.in_(statuses))
    if q:
        pattern = f"%{escape_like(q)}%"
        number_pattern = f"%{escape_like(q.replace(' ', '').upper())}%"
        filters.append(
            or_(
                Project.name.ilike(pattern, escape="\\"),
                exists(
                    select(ProjectNumber.id).where(
                        ProjectNumber.company_id == company_id,
                        ProjectNumber.project_id == Project.id,
                        ProjectNumber.normalised.like(number_pattern, escape="\\"),
                    )
                ),
            )
        )
    return filters


async def list_projects(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    statuses: Sequence[ProjectStatus],
    q: str | None,
    order_by: Sequence[ColumnElement[Any] | InstrumentedAttribute[Any]],
    page: PageParams,
) -> tuple[Sequence[Project], int]:
    filters = _project_filters(company_id, statuses, q)
    total = await db.scalar(select(func.count()).select_from(Project).where(*filters))
    rows = await db.scalars(
        select(Project)
        .where(*filters)
        .order_by(*order_by, Project.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def numbers_of(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> Sequence[ProjectNumber]:
    rows = await db.scalars(
        select(ProjectNumber)
        .where(ProjectNumber.company_id == company_id, ProjectNumber.project_id.in_(project_ids))
        .order_by(ProjectNumber.created_at)
    )
    return rows.all()


async def find_by_number(
    db: AsyncSession, company_id: uuid.UUID, normalised: str
) -> ProjectNumber | None:
    found: ProjectNumber | None = await db.scalar(
        select(ProjectNumber).where(
            ProjectNumber.company_id == company_id, ProjectNumber.normalised == normalised
        )
    )
    return found


async def lock_numbering(
    db: AsyncSession, company_id: uuid.UUID, default_format: str
) -> ProjectNumbering:
    """The company's numbering row, created with the default format on first use, and locked
    until the caller's transaction ends: two projects created at once draw different numbers."""
    await db.execute(
        insert(ProjectNumbering)
        .values(company_id=company_id, format=default_format, next_seq=1)
        .on_conflict_do_nothing(index_elements=[ProjectNumbering.company_id])
    )
    numbering = await db.scalar(
        select(ProjectNumbering)
        .where(ProjectNumbering.company_id == company_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if numbering is None:  # inserted above, in this transaction
        raise RuntimeError("project numbering vanished")
    return numbering


async def get_numbering(db: AsyncSession, company_id: uuid.UUID) -> ProjectNumbering | None:
    numbering: ProjectNumbering | None = await db.scalar(
        select(ProjectNumbering).where(ProjectNumbering.company_id == company_id)
    )
    return numbering


# Parties on a project


async def list_project_parties(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> Sequence[ProjectParty]:
    rows = await db.scalars(
        select(ProjectParty)
        .where(ProjectParty.company_id == company_id, ProjectParty.project_id.in_(project_ids))
        .order_by(ProjectParty.layer, ProjectParty.role, ProjectParty.created_at)
    )
    return rows.all()


async def get_project_party(
    db: AsyncSession, company_id: uuid.UUID, project_party_id: uuid.UUID
) -> ProjectParty | None:
    row: ProjectParty | None = await db.scalar(
        select(ProjectParty).where(
            ProjectParty.company_id == company_id, ProjectParty.id == project_party_id
        )
    )
    return row


async def delete(db: AsyncSession, row: Any) -> None:
    await db.delete(row)
    await db.flush()


# Links


async def get_link(
    db: AsyncSession, company_id: uuid.UUID, link_id: uuid.UUID, *, for_update: bool = False
) -> ProjectLink | None:
    statement = select(ProjectLink).where(
        ProjectLink.company_id == company_id, ProjectLink.id == link_id
    )
    if for_update:
        statement = statement.with_for_update()
    link: ProjectLink | None = await db.scalar(statement)
    return link


async def get_link_for_item(
    db: AsyncSession,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    item_kind: ItemKind,
    item_id: uuid.UUID,
) -> ProjectLink | None:
    link: ProjectLink | None = await db.scalar(
        select(ProjectLink).where(
            ProjectLink.company_id == company_id,
            ProjectLink.project_id == project_id,
            ProjectLink.item_kind == item_kind,
            ProjectLink.item_id == item_id,
        )
    )
    return link


async def links_item(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> bool:
    found = await db.scalar(
        select(ProjectLink.id)
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.item_kind == item_kind,
            ProjectLink.item_id == item_id,
        )
        .limit(1)
    )
    return found is not None


async def links_of_items(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind, item_ids: Sequence[uuid.UUID]
) -> Sequence[ProjectLink]:
    rows = await db.scalars(
        select(ProjectLink)
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.item_kind == item_kind,
            ProjectLink.item_id.in_(item_ids),
        )
        .order_by(ProjectLink.created_at, ProjectLink.id)
    )
    return rows.all()


async def links_of_project(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[ProjectLink]:
    """A project's evidence, oldest first, without what the owner rejected."""
    rows = await db.scalars(
        select(ProjectLink)
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.project_id == project_id,
            ProjectLink.status != LinkStatus.REJECTED,
        )
        .order_by(ProjectLink.created_at, ProjectLink.id)
    )
    return rows.all()


def _link_filters(
    company_id: uuid.UUID,
    *,
    project_id: uuid.UUID | None,
    shipment_id: uuid.UUID | None,
    statuses: Sequence[LinkStatus],
    item_kind: ItemKind | None,
    item_id: uuid.UUID | None,
) -> Select[tuple[ProjectLink]]:
    statement = select(ProjectLink).where(ProjectLink.company_id == company_id)
    if project_id is not None:
        statement = statement.where(ProjectLink.project_id == project_id)
    if shipment_id is not None:
        statement = statement.where(ProjectLink.shipment_id == shipment_id)
    if statuses:
        statement = statement.where(ProjectLink.status.in_(statuses))
    if item_kind is not None:
        statement = statement.where(ProjectLink.item_kind == item_kind)
    if item_id is not None:
        statement = statement.where(ProjectLink.item_id == item_id)
    return statement


async def list_links(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    project_id: uuid.UUID | None,
    shipment_id: uuid.UUID | None,
    statuses: Sequence[LinkStatus],
    item_kind: ItemKind | None,
    item_id: uuid.UUID | None,
    newest_first: bool,
    page: PageParams,
) -> tuple[Sequence[ProjectLink], int]:
    statement = _link_filters(
        company_id,
        project_id=project_id,
        shipment_id=shipment_id,
        statuses=statuses,
        item_kind=item_kind,
        item_id=item_id,
    )
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    order = ProjectLink.created_at.desc() if newest_first else ProjectLink.created_at
    rows = await db.scalars(
        statement.order_by(order, ProjectLink.id).limit(page.limit).offset(page.offset)
    )
    return rows.all(), total or 0


# Field proposals


async def upsert_open_proposal(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    target: ProposalTarget,
    target_id: uuid.UUID,
    field: str,
    value: Any,
    source: FieldSource,
    evidence: Sequence[uuid.UUID],
) -> FieldProposal:
    """The field's open proposal, replaced by this newer value when there is one."""
    statement = insert(FieldProposal).values(
        company_id=company_id,
        target=target,
        target_id=target_id,
        field=field,
        value=value,
        source=source,
        evidence=list(evidence),
        status=ProposalStatus.OPEN,
    )
    proposal = await db.scalar(
        statement.on_conflict_do_update(
            index_elements=[
                FieldProposal.company_id,
                FieldProposal.target,
                FieldProposal.target_id,
                FieldProposal.field,
            ],
            index_where=FieldProposal.status == ProposalStatus.OPEN,
            set_={
                "value": statement.excluded.value,
                "source": statement.excluded.source,
                "evidence": statement.excluded.evidence,
                "created_at": func.now(),
            },
        )
        .returning(FieldProposal)
        .execution_options(populate_existing=True)
    )
    if proposal is None:  # an upsert always returns its row
        raise RuntimeError("proposal upsert returned nothing")
    return proposal


async def get_proposal(
    db: AsyncSession, company_id: uuid.UUID, proposal_id: uuid.UUID
) -> FieldProposal | None:
    proposal: FieldProposal | None = await db.scalar(
        select(FieldProposal)
        .where(FieldProposal.company_id == company_id, FieldProposal.id == proposal_id)
        .with_for_update()
    )
    return proposal


async def list_proposals(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    statuses: Sequence[ProposalStatus],
    target: ProposalTarget | None,
    target_id: uuid.UUID | None,
    page: PageParams,
) -> tuple[Sequence[FieldProposal], int]:
    statement = select(FieldProposal).where(FieldProposal.company_id == company_id)
    if statuses:
        statement = statement.where(FieldProposal.status.in_(statuses))
    if target is not None:
        statement = statement.where(FieldProposal.target == target)
    if target_id is not None:
        statement = statement.where(FieldProposal.target_id == target_id)
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = await db.scalars(
        statement.order_by(FieldProposal.created_at.desc(), FieldProposal.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def activate_if_draft(db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID) -> None:
    await db.execute(
        update(Project)
        .where(
            Project.company_id == company_id,
            Project.id == project_id,
            Project.status == ProjectStatus.DRAFT,
        )
        .values(status=ProjectStatus.ACTIVE)
        .execution_options(synchronize_session="fetch")
    )
