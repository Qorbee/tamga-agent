import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, case, exists, func, or_, select, text, tuple_, update
from sqlalchemy import delete as delete_rows
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.projects.models import (
    ChecklistMark,
    ChecklistMarkKind,
    FieldProposal,
    FieldSource,
    LinkMethod,
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
        # Read again under the lock: a copy the session already held may be older than what
        # another request committed since.
        statement = statement.with_for_update().execution_options(populate_existing=True)
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
    company_id: uuid.UUID, statuses: Sequence[ProjectStatus], q: str | None, suggested: bool | None
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [Project.company_id == company_id]
    if statuses:
        filters.append(Project.status.in_(statuses))
    if suggested is not None:
        waits = exists(
            select(ProjectLink.id).where(
                ProjectLink.company_id == company_id,
                ProjectLink.project_id == Project.id,
                ProjectLink.status == LinkStatus.SUGGESTED,
                ProjectLink.method != LinkMethod.MAIL,
            )
        )
        filters.append(waits if suggested else ~waits)
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
    suggested: bool | None = None,
    order_by: Sequence[ColumnElement[Any] | InstrumentedAttribute[Any]],
    page: PageParams,
) -> tuple[Sequence[Project], int]:
    filters = _project_filters(company_id, statuses, q, suggested)
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


async def get_links(
    db: AsyncSession, company_id: uuid.UUID, link_ids: Sequence[uuid.UUID], *, for_update: bool
) -> Sequence[ProjectLink]:
    statement = (
        select(ProjectLink)
        .where(ProjectLink.company_id == company_id, ProjectLink.id.in_(link_ids))
        .order_by(ProjectLink.id)
    )
    if for_update:
        statement = statement.with_for_update()
    rows = await db.scalars(statement)
    return rows.all()


async def count_links(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> dict[tuple[uuid.UUID, LinkStatus], int]:
    """How many links each of these projects holds, by status: those the owner answers, not the
    files that follow their mail."""
    rows = await db.execute(
        select(ProjectLink.project_id, ProjectLink.status, func.count())
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.project_id.in_(project_ids),
            ProjectLink.method != LinkMethod.MAIL,
        )
        .group_by(ProjectLink.project_id, ProjectLink.status)
    )
    return {(project_id, status): count for project_id, status, count in rows.tuples()}


async def linked_items(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind
) -> list[uuid.UUID]:
    """Every item of a kind that some project links."""
    rows = await db.scalars(
        select(ProjectLink.item_id)
        .where(ProjectLink.company_id == company_id, ProjectLink.item_kind == item_kind)
        .distinct()
        .order_by(ProjectLink.item_id)
    )
    return list(rows.all())


async def mail_link_counts(db: AsyncSession, company_id: uuid.UUID) -> dict[LinkStatus, int]:
    """How many files' mail links there are, by status."""
    rows = await db.execute(
        select(ProjectLink.status, func.count())
        .where(ProjectLink.company_id == company_id, ProjectLink.method == LinkMethod.MAIL)
        .group_by(ProjectLink.status)
    )
    return dict(rows.tuples().all())


async def upsert_mail_links(db: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Writes files' mail links: a new one is added, and one already there is brought to the
    status given only while it is still a mail link. A link of any other method is left as it is.
    Concurrent writers of one file's link meet in the unique key, not in a second row."""
    if not rows:
        return
    statement = insert(ProjectLink).values(list(rows))
    new = statement.excluded
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ProjectLink.project_id, ProjectLink.item_kind, ProjectLink.item_id],
            set_={
                "status": new.status,
                "confidence": new.confidence,
                "decided_by": None,
                # The time it was decided stays while its answer does.
                "decided_at": case(
                    (ProjectLink.status == new.status, ProjectLink.decided_at),
                    else_=new.decided_at,
                ),
            },
            where=ProjectLink.method == LinkMethod.MAIL,
        ),
        execution_options={"synchronize_session": False},
    )


async def lock_files(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> None:
    """Until the transaction ends, no one else works out these files' mail links: two answers to
    two mails that carried one file each see the other's. Taken in one order, so two callers
    never wait on each other."""
    keys = sorted(f"carry:{company_id}:{document_id}" for document_id in document_ids)
    if keys:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(key, 0))"
                " FROM unnest(CAST(:keys AS text[])) AS key"
            ),
            {"keys": keys},
        )


async def drop_mail_links(
    db: AsyncSession,
    company_id: uuid.UUID,
    document_ids: Sequence[uuid.UUID],
    keep: Sequence[tuple[uuid.UUID, uuid.UUID]],
) -> None:
    """Removes these files' mail links to every project not in ``keep`` (file, project): those no
    mail that carried the file is linked to now."""
    if not document_ids:
        return
    statement = delete_rows(ProjectLink).where(
        ProjectLink.company_id == company_id,
        ProjectLink.item_kind == ItemKind.DOCUMENT,
        ProjectLink.item_id.in_(document_ids),
        ProjectLink.method == LinkMethod.MAIL,
    )
    if keep:
        statement = statement.where(
            tuple_(ProjectLink.item_id, ProjectLink.project_id).not_in(keep)
        )
    await db.execute(statement.execution_options(synchronize_session=False))


async def drop_project_mail_links(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Removes the files a rejected project held only because its mails carried them."""
    await db.execute(
        delete_rows(ProjectLink)
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.project_id == project_id,
            ProjectLink.method == LinkMethod.MAIL,
        )
        .execution_options(synchronize_session=False)
    )


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


async def links_of_projects(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> Sequence[ProjectLink]:
    """These projects' evidence, oldest first, without what the owner rejected."""
    rows = await db.scalars(
        select(ProjectLink)
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.project_id.in_(project_ids),
            ProjectLink.status != LinkStatus.REJECTED,
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


async def links_on_trucks(
    db: AsyncSession,
    company_id: uuid.UUID,
    shipment_ids: Sequence[uuid.UUID],
    *,
    for_update: bool = False,
) -> Sequence[ProjectLink]:
    """Every link placed on one of these trucks."""
    statement = select(ProjectLink).where(
        ProjectLink.company_id == company_id, ProjectLink.shipment_id.in_(shipment_ids)
    )
    rows = await db.scalars(statement.with_for_update() if for_update else statement)
    return rows.all()


async def suggest_truck(
    db: AsyncSession, company_id: uuid.UUID, link_id: uuid.UUID, shipment_id: uuid.UUID
) -> bool:
    """Sets the truck on a link still code's and on no truck, as the row stands now."""
    result = await db.execute(
        update(ProjectLink)
        .where(
            ProjectLink.company_id == company_id,
            ProjectLink.id == link_id,
            ProjectLink.method != LinkMethod.OWNER,
            ProjectLink.shipment_id.is_(None),
        )
        .values(shipment_id=shipment_id)
        .returning(ProjectLink.id)
        .execution_options(synchronize_session="fetch")
    )
    return result.first() is not None


async def open_projects(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Project]:
    rows = await db.scalars(
        select(Project)
        .where(Project.company_id == company_id, Project.status != ProjectStatus.REJECTED)
        .order_by(Project.id)
    )
    return rows.all()


async def companies_with_projects(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Project.company_id).where(Project.status != ProjectStatus.REJECTED).distinct()
    )
    return list(rows.all())


async def all_links_of_project(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[ProjectLink]:
    """Every link of a project, the rejected ones too, locked: for moving them."""
    rows = await db.scalars(
        select(ProjectLink)
        .where(ProjectLink.company_id == company_id, ProjectLink.project_id == project_id)
        .order_by(ProjectLink.created_at, ProjectLink.id)
        .with_for_update()
    )
    return rows.all()


async def move_numbers(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    await db.execute(
        update(ProjectNumber)
        .where(ProjectNumber.company_id == company_id, ProjectNumber.project_id == source_id)
        .values(project_id=target_id)
    )


async def drop_proposals(
    db: AsyncSession, company_id: uuid.UUID, target: ProposalTarget, target_id: uuid.UUID
) -> None:
    """Removes what was proposed for a row that is going."""
    await db.execute(
        delete_rows(FieldProposal).where(
            FieldProposal.company_id == company_id,
            FieldProposal.target == target,
            FieldProposal.target_id == target_id,
        )
    )


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


# Checklist marks


async def marks_of_projects(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> Sequence[ChecklistMark]:
    rows = await db.scalars(
        select(ChecklistMark).where(
            ChecklistMark.company_id == company_id, ChecklistMark.project_id.in_(project_ids)
        )
    )
    return rows.all()


async def set_mark(
    db: AsyncSession,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    shipment_id: uuid.UUID | None,
    key: str,
    mark: ChecklistMarkKind,
) -> ChecklistMark:
    statement = (
        insert(ChecklistMark)
        .values(
            company_id=company_id,
            project_id=project_id,
            shipment_id=shipment_id,
            key=key,
            mark=mark,
        )
        .on_conflict_do_update(
            index_elements=["company_id", "project_id", "shipment_id", "key"],
            set_={"mark": mark, "updated_at": func.now()},
        )
        .returning(ChecklistMark)
        .execution_options(populate_existing=True)
    )
    return (await db.scalars(statement)).one()


async def get_mark(
    db: AsyncSession, company_id: uuid.UUID, mark_id: uuid.UUID
) -> ChecklistMark | None:
    rows = await db.scalars(
        select(ChecklistMark).where(
            ChecklistMark.company_id == company_id, ChecklistMark.id == mark_id
        )
    )
    return rows.first()


async def marked_trucks(
    db: AsyncSession, company_id: uuid.UUID, shipment_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    rows = await db.scalars(
        select(ChecklistMark.shipment_id).where(
            ChecklistMark.company_id == company_id, ChecklistMark.shipment_id.in_(shipment_ids)
        )
    )
    return {row for row in rows.all() if row is not None}


async def move_marks(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """Moves a merged project's own marks where the project that stays has none for the item;
    the rest go with the project."""
    kept = select(ChecklistMark.key).where(
        ChecklistMark.company_id == company_id,
        ChecklistMark.project_id == target_id,
        ChecklistMark.shipment_id.is_(None),
    )
    await db.execute(
        update(ChecklistMark)
        .where(
            ChecklistMark.company_id == company_id,
            ChecklistMark.project_id == source_id,
            ChecklistMark.shipment_id.is_(None),
            ChecklistMark.key.not_in(kept),
        )
        .values(project_id=target_id)
        .execution_options(synchronize_session=False)
    )
