import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.memory.models import MemoryEntry, MemoryKind, MemorySource, MemoryState
from app.platform import search
from app.platform.pagination import PageParams, escape_like


async def add(db: AsyncSession, entry: MemoryEntry) -> MemoryEntry:
    db.add(entry)
    await db.flush()
    return entry


async def get(
    db: AsyncSession, company_id: uuid.UUID, entry_id: uuid.UUID, *, for_update: bool = False
) -> MemoryEntry | None:
    query = select(MemoryEntry).where(
        MemoryEntry.company_id == company_id, MemoryEntry.id == entry_id
    )
    if for_update:
        query = query.with_for_update()
    entry: MemoryEntry | None = await db.scalar(query)
    return entry


async def delete(db: AsyncSession, entry: MemoryEntry) -> None:
    await db.delete(entry)
    await db.flush()


async def list_entries(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    states: Sequence[MemoryState] | None,
    kinds: Sequence[MemoryKind] | None,
    party_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    chat_id: uuid.UUID | None,
    q: str | None,
    sort: str,
    page: PageParams,
) -> tuple[Sequence[MemoryEntry], int]:
    filters: list[ColumnElement[bool]] = [MemoryEntry.company_id == company_id]
    if states:
        filters.append(MemoryEntry.state.in_(states))
    if kinds:
        filters.append(MemoryEntry.kind.in_(kinds))
    if party_id is not None:
        filters.append(MemoryEntry.party_id == party_id)
    if project_id is not None:
        filters.append(MemoryEntry.project_id == project_id)
    if chat_id is not None:
        filters.append(MemoryEntry.chat_id == chat_id)
    if q:
        filters.append(_matches(q))
    total = await db.scalar(select(func.count()).select_from(MemoryEntry).where(*filters))
    rows = await db.scalars(
        select(MemoryEntry)
        .where(*filters)
        .order_by(*_SORTS[sort], MemoryEntry.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


_SORTS: dict[str, list[Any]] = {
    "-updated_at": [MemoryEntry.updated_at.desc()],
    "-last_used_at": [MemoryEntry.last_used_at.desc().nulls_last()],
    "kind": [MemoryEntry.kind.asc(), MemoryEntry.updated_at.desc()],
}


async def held(
    db: AsyncSession, company_id: uuid.UUID, limit: int, kinds: Sequence[MemoryKind] | None
) -> Sequence[MemoryEntry]:
    """The entries agents read, of these kinds or all, the most recently confirmed or changed
    first: what is read most does not keep what was taught since from the list."""
    filters = [MemoryEntry.company_id == company_id, MemoryEntry.state == MemoryState.HELD]
    if kinds:
        filters.append(MemoryEntry.kind.in_(kinds))
    rows = await db.scalars(
        select(MemoryEntry)
        .where(*filters)
        .order_by(
            MemoryEntry.confirmed_at.desc().nulls_last(),
            MemoryEntry.updated_at.desc(),
            MemoryEntry.id.desc(),
        )
        .limit(limit)
    )
    return rows.all()


async def touch(
    db: AsyncSession, company_id: uuid.UUID, entry_ids: Sequence[uuid.UUID], at: datetime
) -> None:
    if not entry_ids:
        return
    await db.execute(
        update(MemoryEntry)
        .where(MemoryEntry.company_id == company_id, MemoryEntry.id.in_(entry_ids))
        .values(last_used_at=at)
    )


async def move_project(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    await db.execute(
        update(MemoryEntry)
        .where(MemoryEntry.company_id == company_id, MemoryEntry.project_id == source_id)
        .values(project_id=target_id)
    )


def _matches(q: str) -> ColumnElement[bool]:
    """Every term of the query found in the entry, both folded alike (`platform/search.py`)."""
    return and_(true(), *(_found(term) for term in search.terms(q)))


def _found(term: search.Term) -> ColumnElement[bool]:
    if term.anywhere:
        compact = func.replace(MemoryEntry.search_text, " ", "")
        return compact.like(f"%{escape_like(term.compact)}%", escape="\\")
    return MemoryEntry.search_text.like(f"% {escape_like(term.words)}%", escape="\\")


async def learned_since(
    db: AsyncSession, company_id: uuid.UUID, since: datetime
) -> Sequence[MemoryEntry]:
    found = await db.scalars(
        select(MemoryEntry)
        .where(
            MemoryEntry.company_id == company_id,
            MemoryEntry.source == MemorySource.LEARNER,
            MemoryEntry.state == MemoryState.PROPOSED,
            MemoryEntry.created_at >= since,
        )
        .order_by(MemoryEntry.created_at)
    )
    return found.all()
