import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import any_, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.linking.models import KeptReading, Outcome, Placement
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


async def keep_waiting(
    db: AsyncSession, company_id: uuid.UUID, thread_ids: Sequence[uuid.UUID]
) -> None:
    await db.execute(
        update(Placement)
        .where(Placement.company_id == company_id, Placement.thread_id.in_(thread_ids))
        .values(waiting=True)
        .execution_options(synchronize_session=False)
    )


async def wake_due(db: AsyncSession, company_id: uuid.UUID, now: datetime) -> None:
    """Marks as waiting the conversations whose wait for their files is over."""
    await db.execute(
        update(Placement)
        .where(Placement.company_id == company_id, Placement.look_again_at <= now)
        .values(waiting=True, look_again_at=None)
        .execution_options(synchronize_session=False)
    )


# When a conversation was first noted, the order its readings are told in.
type Noted = tuple[datetime, uuid.UUID]


@dataclass(frozen=True, slots=True)
class Kept:
    project_id: uuid.UUID
    thread_id: uuid.UUID
    reading: dict[str, Any]
    noted: Noted


async def readings_of_projects(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[Noted, dict[str, Any]]]]:
    """What was read in each conversation placed in each project, with when the conversation was
    first noted: the one a project was proposed for comes first. A project nothing was read for
    is left out."""
    if not project_ids:
        return {}
    rows = await db.execute(
        select(Placement.project_id, Placement.reading, Placement.created_at, Placement.id)
        .where(
            Placement.company_id == company_id,
            Placement.project_id.in_(project_ids),
            Placement.outcome.in_((Outcome.LINKED, Outcome.PROPOSED)),
            # A reading stored as JSON null before such were stored as SQL NULL is none either.
            func.jsonb_typeof(Placement.reading) == "object",
        )
        .order_by(Placement.created_at, Placement.id)
    )
    readings: dict[uuid.UUID, list[tuple[Noted, dict[str, Any]]]] = {}
    for project_id, reading, created_at, placement_id in rows.all():
        readings.setdefault(project_id, []).append(((created_at, placement_id), reading))
    return readings


async def readings_by_thread(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID, dict[str, Any]]]:
    """Each reading a conversation has in each project, its own or kept there: (project,
    conversation, reading)."""
    if not project_ids:
        return []
    placed = await db.execute(
        select(Placement.project_id, Placement.thread_id, Placement.reading).where(
            Placement.company_id == company_id,
            Placement.project_id.in_(project_ids),
            Placement.outcome.in_((Outcome.LINKED, Outcome.PROPOSED)),
            func.jsonb_typeof(Placement.reading) == "object",
        )
    )
    kept = await db.execute(
        select(KeptReading.project_id, KeptReading.thread_id, KeptReading.reading).where(
            KeptReading.company_id == company_id, KeptReading.project_id.in_(project_ids)
        )
    )
    return [
        (project_id, thread_id, reading)
        for project_id, thread_id, reading in [*placed.tuples().all(), *kept.tuples().all()]
        if project_id is not None and reading
    ]


async def kept_readings(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> list[Kept]:
    """The readings kept for these projects, each with when its conversation was first noted."""
    if not project_ids:
        return []
    rows = await db.execute(
        select(
            KeptReading.project_id,
            KeptReading.thread_id,
            KeptReading.reading,
            Placement.created_at,
            Placement.id,
        )
        .join(
            Placement,
            (Placement.company_id == KeptReading.company_id)
            & (Placement.thread_id == KeptReading.thread_id),
        )
        .where(KeptReading.company_id == company_id, KeptReading.project_id.in_(project_ids))
    )
    return [
        Kept(project_id, thread_id, reading, (created_at, placement_id))
        for project_id, thread_id, reading, created_at, placement_id in rows.all()
    ]


async def keep_reading(
    db: AsyncSession,
    company_id: uuid.UUID,
    thread_id: uuid.UUID,
    project_id: uuid.UUID,
    reading: dict[str, Any],
) -> None:
    statement = insert(KeptReading).values(
        company_id=company_id, thread_id=thread_id, project_id=project_id, reading=reading
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["company_id", "thread_id", "project_id"],
            set_={"reading": statement.excluded.reading},
        )
    )


async def move_kept_readings(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """A merged project's kept readings are the other's. Where both kept the same conversation's,
    the other's keeps its values and takes what only the merged one read; the merged one's row
    goes with its project."""
    rows = await db.scalars(
        select(KeptReading).where(
            KeptReading.company_id == company_id,
            KeptReading.project_id.in_((source_id, target_id)),
        )
    )
    kept = rows.all()
    there = {row.thread_id: row for row in kept if row.project_id == target_id}
    for row in kept:
        if row.project_id != source_id:
            continue
        if (other := there.get(row.thread_id)) is not None:
            other.reading = {**row.reading, **other.reading}
        else:
            row.project_id = target_id


async def read_projects(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    """The projects a conversation was read into, and those keeping what one read there before
    its later mails went to another project."""
    placed = select(Placement.project_id).where(
        Placement.company_id == company_id,
        Placement.project_id.is_not(None),
        Placement.reading.is_not(None),
    )
    kept = select(KeptReading.project_id).where(KeptReading.company_id == company_id)
    rows = await db.scalars(placed.union(kept))
    return [project_id for project_id in rows if project_id is not None]


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


async def threads_in(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID, *, limit: int
) -> list[uuid.UUID]:
    """The conversations placed in a project, the one it was proposed for first."""
    rows = await db.scalars(
        select(Placement.thread_id)
        .where(
            Placement.company_id == company_id,
            Placement.project_id == project_id,
            Placement.outcome.in_((Outcome.LINKED, Outcome.PROPOSED)),
        )
        .order_by(Placement.created_at, Placement.id)
        .limit(limit)
    )
    return list(rows)


async def companies_awaiting_owner(db: AsyncSession) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone: those holding a placement that waits
    for the owner's answer."""
    rows = await db.scalars(
        select(Placement.company_id)
        .where(Placement.outcome.in_([Outcome.PROPOSED, Outcome.ASKED]))
        .distinct()
    )
    return list(rows)


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


async def placed_in_projects(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Placement]:
    """The conversations placed in a project, by a rule or a model."""
    rows = await db.scalars(
        select(Placement)
        .where(Placement.company_id == company_id, Placement.project_id.is_not(None))
        .order_by(Placement.id)
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
