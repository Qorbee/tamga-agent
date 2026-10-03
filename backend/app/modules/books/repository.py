import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books.models import BooksCursor, BooksReads, Dispatch, Invoice, InvoiceDirection
from app.platform.pagination import PageParams, escape_like


async def upsert_invoice(db: AsyncSession, values: dict[str, Any]) -> None:
    statement = insert(Invoice).values(**values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[Invoice.company_id, Invoice.source, Invoice.source_id],
            set_={
                key: statement.excluded[key]
                for key in values
                if key not in ("company_id", "source", "source_id")
            }
            | {"synced_at": func.now()},
        )
    )


async def upsert_dispatch(db: AsyncSession, values: dict[str, Any]) -> None:
    statement = insert(Dispatch).values(**values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[Dispatch.company_id, Dispatch.source, Dispatch.source_id],
            set_={
                key: statement.excluded[key]
                for key in values
                if key not in ("company_id", "source", "source_id")
            }
            | {"synced_at": func.now()},
        )
    )


async def get_cursor(
    db: AsyncSession, company_id: uuid.UUID, source: str, resource: str
) -> date | None:
    through: date | None = await db.scalar(
        select(BooksCursor.synced_through).where(
            BooksCursor.company_id == company_id,
            BooksCursor.source == source,
            BooksCursor.resource == resource,
        )
    )
    return through


async def save_cursor(
    db: AsyncSession, company_id: uuid.UUID, source: str, resource: str, through: date
) -> None:
    statement = insert(BooksCursor).values(
        company_id=company_id, source=source, resource=resource, synced_through=through
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[BooksCursor.company_id, BooksCursor.source, BooksCursor.resource],
            set_={"synced_through": statement.excluded.synced_through, "updated_at": func.now()},
        )
    )


async def spend_read(
    db: AsyncSession, company_id: uuid.UUID, source: str, month: date, limit: int
) -> int | None:
    """The month's read count after this one, or None when the budget was already spent. One
    statement, so two syncs spending at once never pass the limit together."""
    statement = insert(BooksReads).values(
        company_id=company_id, source=source, month=month, reads=1
    )
    reads: int | None = await db.scalar(
        statement.on_conflict_do_update(
            index_elements=[BooksReads.company_id, BooksReads.source, BooksReads.month],
            set_={"reads": BooksReads.reads + 1},
            where=BooksReads.reads < limit,
        ).returning(BooksReads.reads)
    )
    return reads


async def payable_parties(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Invoice.party_id)
        .where(
            Invoice.company_id == company_id,
            Invoice.direction == InvoiceDirection.PAYABLE,
            Invoice.party_id.is_not(None),
        )
        .distinct()
        # In one order, so a registration tried again names its payees as it first did.
        .order_by(Invoice.party_id)
    )
    return [party_id for party_id in rows if party_id is not None]


async def invoice_exists(db: AsyncSession, company_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(Invoice.id).where(Invoice.company_id == company_id, Invoice.id == invoice_id)
    )
    return found is not None


async def list_invoices(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    direction: InvoiceDirection | None,
    party_id: uuid.UUID | None,
    q: str | None,
    page: PageParams,
) -> tuple[Sequence[Invoice], int]:
    filters: list[ColumnElement[bool]] = [Invoice.company_id == company_id]
    if direction is not None:
        filters.append(Invoice.direction == direction)
    if party_id is not None:
        filters.append(Invoice.party_id == party_id)
    if q:
        pattern = f"%{escape_like(q)}%"
        filters.append(
            or_(
                Invoice.number.ilike(pattern, escape="\\"),
                Invoice.party_name.ilike(pattern, escape="\\"),
                Invoice.e_document_id.ilike(pattern, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(Invoice).where(*filters))
    rows = await db.scalars(
        select(Invoice)
        .where(*filters)
        .order_by(Invoice.issue_date.desc().nulls_last(), Invoice.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def find_invoices(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None,
    since: date | None,
    until: date | None,
    limit: int,
) -> Sequence[Invoice]:
    filters: list[ColumnElement[bool]] = [Invoice.company_id == company_id]
    if q:
        pattern = f"%{escape_like(q)}%"
        filters.append(
            or_(
                Invoice.number.ilike(pattern, escape="\\"),
                Invoice.party_name.ilike(pattern, escape="\\"),
                Invoice.e_document_id.ilike(pattern, escape="\\"),
            )
        )
    if since is not None:
        filters.append(Invoice.issue_date >= since)
    if until is not None:
        filters.append(Invoice.issue_date <= until)
    rows = await db.scalars(
        select(Invoice)
        .where(*filters)
        .order_by(Invoice.issue_date.desc().nulls_last(), Invoice.id)
        .limit(limit)
    )
    return rows.all()


async def get_invoices(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Invoice]:
    rows = await db.scalars(
        select(Invoice).where(Invoice.company_id == company_id, Invoice.id.in_(ids))
    )
    return rows.all()


async def invoices_named(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    numbers: Sequence[str],
    e_document_ids: Sequence[str],
) -> Sequence[Invoice]:
    """Invoices whose number, letters and digits only and in upper case, is one of ``numbers``,
    or whose e-document id is one of ``e_document_ids`` whatever its case."""
    bare = func.upper(func.regexp_replace(Invoice.number, "[^0-9A-Za-z]", "", "g"))
    rows = await db.scalars(
        select(Invoice).where(
            Invoice.company_id == company_id,
            or_(bare.in_(numbers), func.lower(Invoice.e_document_id).in_(e_document_ids)),
        )
    )
    return rows.all()
