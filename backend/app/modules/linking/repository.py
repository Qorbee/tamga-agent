import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import any_, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.linking.models import Outcome, Placement
from app.platform.pagination import PageParams


async def latest_seen(db: AsyncSession, company_id: uuid.UUID) -> datetime | None:
    """The newest change of a conversation this company's placements took note of."""
    seen: datetime | None = await db.scalar(
        select(func.max(Placement.thread_changed_at)).where(Placement.company_id == company_id)
    )
    return seen


async def note_changed(
    db: AsyncSession, company_id: uuid.UUID, thread_id: uuid.UUID, changed_at: datetime
) -> None:
    """Marks a conversation as waiting to be read, unless this change of it was noted before:
    the changes are looked through with an overlap, and one seen twice must not be read twice."""
    row = insert(Placement).values(
        id=uuid.uuid7(),
        company_id=company_id,
        thread_id=thread_id,
        waiting=True,
        thread_changed_at=changed_at,
    )
    await db.execute(
        row.on_conflict_do_update(
            index_elements=[Placement.company_id, Placement.thread_id],
            set_={"waiting": True, "thread_changed_at": row.excluded.thread_changed_at},
            where=Placement.thread_changed_at < row.excluded.thread_changed_at,
        )
    )


async def readings_of_project(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID
) -> list[tuple[uuid.UUID, dict[str, Any]]]:
    """What was read in each conversation placed in the project, by placement, in the order the
    conversations were first noted: the one a project was proposed for comes first."""
    rows = await db.execute(
        select(Placement.id, Placement.reading)
        .where(
            Placement.company_id == company_id,
            Placement.project_id == project_id,
            Placement.outcome.in_((Outcome.LINKED, Outcome.PROPOSED)),
            Placement.reading.is_not(None),
        )
        .order_by(Placement.created_at, Placement.id)
    )
    return [(placement_id, reading) for placement_id, reading in rows.all()]


async def naming(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[Placement]:
    """The placements that name a project, as the one a conversation is in or as one it could be
    in, locked."""
    rows = await db.scalars(
        select(Placement)
        .where(
            Placement.company_id == company_id,
            (Placement.project_id == project_id)
            | (literal(project_id) == any_(Placement.alternatives)),
        )
        .order_by(Placement.id)
        .with_for_update()
    )
    return rows.all()


async def waiting(
    db: AsyncSession, company_id: uuid.UUID, *, tries: int, retry_before: datetime, limit: int
) -> Sequence[uuid.UUID]:
    """The conversations that wait, in the order they became ready; one the model could not be
    reached for ``tries`` times waits until ``retry_before`` has passed its last failure."""
    rows = await db.scalars(
        select(Placement.thread_id)
        .where(
            Placement.company_id == company_id,
            Placement.waiting,
            (Placement.failures < tries) | (Placement.failed_at <= retry_before),
        )
        .order_by(Placement.thread_changed_at, Placement.id)
        .limit(limit)
    )
    return rows.all()


async def get(
    db: AsyncSession, company_id: uuid.UUID, thread_id: uuid.UUID, *, for_update: bool = False
) -> Placement | None:
    statement = select(Placement).where(
        Placement.company_id == company_id, Placement.thread_id == thread_id
    )
    if for_update:
        statement = statement.with_for_update()
    placement: Placement | None = await db.scalar(statement)
    return placement


async def get_by_id(
    db: AsyncSession, company_id: uuid.UUID, placement_id: uuid.UUID, *, for_update: bool = False
) -> Placement | None:
    statement = select(Placement).where(
        Placement.company_id == company_id, Placement.id == placement_id
    )
    if for_update:
        statement = statement.with_for_update()
    placement: Placement | None = await db.scalar(statement)
    return placement


async def list_placements(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    outcomes: Sequence[Outcome],
    project_ids: Sequence[uuid.UUID],
    page: PageParams,
) -> tuple[Sequence[Placement], int]:
    """Placements, the most recently placed first, those never read last."""
    filters = [Placement.company_id == company_id]
    if outcomes:
        filters.append(Placement.outcome.in_(outcomes))
    if project_ids:
        filters.append(Placement.project_id.in_(project_ids))
    total = await db.scalar(select(func.count()).select_from(Placement).where(*filters))
    rows = await db.scalars(
        select(Placement)
        .where(*filters)
        .order_by(Placement.placed_at.desc().nulls_last(), Placement.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def add_failure(
    db: AsyncSession, company_id: uuid.UUID, thread_id: uuid.UUID, at: datetime
) -> None:
    await db.execute(
        update(Placement)
        .where(Placement.company_id == company_id, Placement.thread_id == thread_id)
        .values(failures=Placement.failures + 1, failed_at=at)
    )
