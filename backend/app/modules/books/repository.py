import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Row, and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books.models import (
    BooksCursor,
    BooksReads,
    Dispatch,
    Invoice,
    InvoiceDirection,
    InvoiceKind,
)
from app.platform.pagination import PageParams, escape_like


async def upsert_invoice(
    db: AsyncSession,
    values: dict[str, Any],
    *,
    same_as_source_id: str | None = None,
    read: bool = True,
) -> uuid.UUID:
    """``same_as_source_id``: the source's id of the record of the same bill in another list,
    resolved in the statement itself. ``read``: the values come from a read of the source, which
    ``synced_at`` dates; a remap of a stored record keeps the date of its read."""
    same_as = (
        select(Invoice.id)
        .where(
            Invoice.company_id == values["company_id"],
            Invoice.source == values["source"],
            Invoice.source_id == same_as_source_id,
        )
        .scalar_subquery()
        if same_as_source_id
        else None
    )
    statement = insert(Invoice).values(**values, same_as_id=same_as)
    stored = await db.execute(
        statement.on_conflict_do_update(
            index_elements=[Invoice.company_id, Invoice.source, Invoice.source_id],
            set_={
                key: statement.excluded[key]
                for key in (*values, "same_as_id")
                if key not in ("company_id", "source", "source_id")
            }
            | ({"synced_at": func.now()} if read else {}),
        ).returning(Invoice.id)
    )
    invoice_id: uuid.UUID = stored.scalar_one()
    return invoice_id


async def placeable_invoices(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Invoice]:
    rows = await db.scalars(
        select(Invoice)
        .where(
            Invoice.company_id == company_id,
            Invoice.kind.in_(
                [InvoiceKind.SALES, InvoiceKind.PURCHASE, InvoiceKind.INCOMING_E_INVOICE]
            ),
            Invoice.cancelled.is_(False),
        )
        .order_by(Invoice.id)
    )
    return rows.all()


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


def _open_payable() -> ColumnElement[bool]:
    return (
        (Invoice.kind == InvoiceKind.PURCHASE)
        & Invoice.cancelled.is_(False)
        & Invoice.party_id.is_not(None)
        & (Invoice.remaining_amount > 0)
    )


async def open_on_account(
    db: AsyncSession, company_id: uuid.UUID, party_ids: Sequence[uuid.UUID]
) -> Sequence[Invoice]:
    """These firms' booked invoices the books show open: sales, purchases and expenses, not
    cancelled, with something left. An incoming e-invoice is on the account as its purchase."""
    if not party_ids:
        return []
    rows = await db.scalars(
        select(Invoice).where(
            Invoice.company_id == company_id,
            Invoice.party_id.in_(party_ids),
            Invoice.kind.in_((InvoiceKind.SALES, InvoiceKind.PURCHASE, InvoiceKind.EXPENSE)),
            Invoice.cancelled.is_(False),
            Invoice.remaining_amount > 0,
        )
    )
    return rows.all()


async def open_payables(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Invoice]:
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.company_id == company_id, _open_payable())
        .order_by(Invoice.due_date.asc().nulls_last(), Invoice.id)
    )
    return rows.all()


async def companies_with_payables(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(select(Invoice.company_id).where(_open_payable()).distinct())
    return list(rows)


def _settled_in_part() -> ColumnElement[bool]:
    return (
        Invoice.kind.in_((InvoiceKind.SALES, InvoiceKind.PURCHASE, InvoiceKind.EXPENSE))
        & Invoice.cancelled.is_(False)
        & (Invoice.total_amount > 0)
        & Invoice.remaining_amount.is_not(None)
        & (Invoice.remaining_amount < Invoice.total_amount)
    )


async def settled_in_part(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Invoice]:
    rows = await db.scalars(
        select(Invoice).where(Invoice.company_id == company_id, _settled_in_part())
    )
    return rows.all()


async def companies_with_settled(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(select(Invoice.company_id).where(_settled_in_part()).distinct())
    return list(rows)


async def namesakes(db: AsyncSession, company_id: uuid.UUID, invoice: Invoice) -> list[uuid.UUID]:
    """Other purchases booked with the same e-document id, or with the same number from the same
    party: the same bill booked twice."""
    same = []
    if invoice.e_document_id:
        same.append(Invoice.e_document_id == invoice.e_document_id)
    if invoice.number and invoice.party_id is not None:
        same.append((Invoice.number == invoice.number) & (Invoice.party_id == invoice.party_id))
    if not same:
        return []
    rows = await db.scalars(
        select(Invoice.id).where(
            Invoice.company_id == company_id,
            Invoice.kind == InvoiceKind.PURCHASE,
            Invoice.id != invoice.id,
            or_(*same),
        )
    )
    return list(rows)


async def invoice_exists(db: AsyncSession, company_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(Invoice.id).where(Invoice.company_id == company_id, Invoice.id == invoice_id)
    )
    return found is not None


@dataclass(frozen=True, slots=True)
class InvoiceFilter:
    """Which of a company's invoices a list or its totals hold."""

    direction: InvoiceDirection | None = None
    kinds: Sequence[InvoiceKind] | None = None
    party_id: uuid.UUID | None = None
    # True: not cancelled and something left to pay. False: cancelled, or nothing left.
    outstanding: bool | None = None
    # Due before this day.
    due_before: date | None = None
    q: str | None = None

    def where(self, company_id: uuid.UUID) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = [Invoice.company_id == company_id]
        if self.direction is not None:
            filters.append(Invoice.direction == self.direction)
        if self.kinds:
            filters.append(Invoice.kind.in_(self.kinds))
        if self.party_id is not None:
            filters.append(Invoice.party_id == self.party_id)
        if self.outstanding is True:
            filters.append(and_(Invoice.cancelled.is_(False), Invoice.remaining_amount > 0))
        elif self.outstanding is False:
            filters.append(or_(Invoice.cancelled.is_(True), Invoice.remaining_amount <= 0))
        if self.due_before is not None:
            filters.append(Invoice.due_date < self.due_before)
        if self.q:
            pattern = f"%{escape_like(self.q)}%"
            filters.append(
                or_(
                    Invoice.number.ilike(pattern, escape="\\"),
                    Invoice.party_name.ilike(pattern, escape="\\"),
                    Invoice.e_document_id.ilike(pattern, escape="\\"),
                )
            )
        return filters


async def list_invoices(
    db: AsyncSession,
    company_id: uuid.UUID,
    which: InvoiceFilter,
    *,
    sort: str = "-issue_date",
    page: PageParams,
) -> tuple[Sequence[Invoice], int]:
    filters = which.where(company_id)
    total = await db.scalar(select(func.count()).select_from(Invoice).where(*filters))
    rows = await db.scalars(
        select(Invoice)
        .where(*filters)
        .order_by(*_INVOICE_SORTS[sort], Invoice.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def invoice_totals(
    db: AsyncSession, company_id: uuid.UUID, which: InvoiceFilter
) -> Sequence[Row[tuple[str | None, int, Decimal | None, Decimal | None, Decimal | None]]]:
    """Per currency: how many invoices, their totals, and what is left and what was paid of those
    not cancelled. A figure is null when the books give the amounts of only some of them: a
    partial sum would read as the whole."""
    live = Invoice.cancelled.is_(False)
    total = case(
        (
            func.count(Invoice.total_amount) == func.count(),
            func.coalesce(func.sum(Invoice.total_amount), 0),
        )
    )
    left = case(
        (
            func.count(Invoice.remaining_amount).filter(live) == func.count().filter(live),
            func.coalesce(func.sum(Invoice.remaining_amount).filter(live), 0),
        )
    )
    paid = case(
        (
            func.count(Invoice.total_amount - Invoice.remaining_amount).filter(live)
            == func.count().filter(live),
            func.coalesce(
                func.sum(Invoice.total_amount - Invoice.remaining_amount).filter(live), 0
            ),
        )
    )
    rows = await db.execute(
        select(Invoice.currency, func.count(), total, left, paid)
        .where(*which.where(company_id))
        .group_by(Invoice.currency)
        .order_by(func.count().desc(), Invoice.currency)
    )
    return rows.all()


async def open_invoices(
    db: AsyncSession, company_id: uuid.UUID, which: InvoiceFilter
) -> Sequence[Invoice]:
    """Every invoice a list holds that the books show open: not cancelled, something left."""
    rows = await db.scalars(
        select(Invoice).where(
            *which.where(company_id),
            Invoice.cancelled.is_(False),
            Invoice.remaining_amount > 0,
        )
    )
    return rows.all()


_INVOICE_SORTS: dict[str, list[ColumnElement[Any]]] = {
    "-issue_date": [Invoice.issue_date.desc().nulls_last()],
    "issue_date": [Invoice.issue_date.asc().nulls_last()],
    "due_date": [Invoice.due_date.asc().nulls_last()],
    "-due_date": [Invoice.due_date.desc().nulls_last()],
    "-total_amount": [Invoice.total_amount.desc().nulls_last()],
    "-remaining_amount": [Invoice.remaining_amount.desc().nulls_last()],
}


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


async def invoices_of_source(
    db: AsyncSession, company_id: uuid.UUID, source: str
) -> Sequence[Invoice]:
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.company_id == company_id, Invoice.source == source)
        .order_by(Invoice.id)
    )
    return rows.all()


async def e_document_of(db: AsyncSession, company_id: uuid.UUID, invoice: Invoice) -> str | None:
    """The e-document id an invoice carries, or the one its other record does: a purchase booked
    from an incoming e-invoice names none itself."""
    if invoice.e_document_id:
        return invoice.e_document_id
    twin: str | None = await db.scalar(
        select(Invoice.e_document_id)
        .where(
            Invoice.company_id == company_id,
            Invoice.same_as_id == invoice.id,
            Invoice.e_document_id.is_not(None),
        )
        .limit(1)
    )
    return twin


async def open_months(db: AsyncSession, company_id: uuid.UUID, source: str) -> list[date]:
    """The first day of each month that holds an invoice of ``source`` still open: what is left of
    it above zero, and not cancelled."""
    month = func.date_trunc("month", Invoice.issue_date)
    rows = await db.scalars(
        select(month)
        .where(
            Invoice.company_id == company_id,
            Invoice.source == source,
            Invoice.remaining_amount > 0,
            Invoice.cancelled.is_(False),
            Invoice.issue_date.is_not(None),
        )
        .group_by(month)
        .order_by(month)
    )
    return [first.date() for first in rows.all()]


async def same_as(
    db: AsyncSession, company_id: uuid.UUID, invoice_ids: Sequence[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    rows = await db.execute(
        select(Invoice.id, Invoice.same_as_id).where(
            Invoice.company_id == company_id, Invoice.same_as_id.in_(invoice_ids)
        )
    )
    return [(alias, target) for alias, target in rows.tuples() if target is not None]


async def get_invoices(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Invoice]:
    rows = await db.scalars(
        select(Invoice).where(Invoice.company_id == company_id, Invoice.id.in_(ids))
    )
    return rows.all()


async def invoice_labels(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str | None, date | None]]:
    rows = await db.execute(
        select(Invoice.id, Invoice.number, Invoice.issue_date).where(
            Invoice.company_id == company_id, Invoice.id.in_(ids)
        )
    )
    return {invoice_id: (number, issued) for invoice_id, number, issued in rows.tuples().all()}


async def kinds_of_parties(
    db: AsyncSession, company_id: uuid.UUID, party_ids: Sequence[uuid.UUID]
) -> Sequence[tuple[uuid.UUID, InvoiceKind]]:
    rows = await db.execute(
        select(Invoice.party_id, Invoice.kind)
        .where(Invoice.company_id == company_id, Invoice.party_id.in_(party_ids))
        .distinct()
    )
    return [(party_id, kind) for party_id, kind in rows.tuples().all() if party_id is not None]


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
