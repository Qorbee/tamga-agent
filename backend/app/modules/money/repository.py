import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.money.models import (
    AccountKind,
    CategorySetBy,
    MoneyAccount,
    MoneyMovement,
    MovementCategory,
)
from app.platform.pagination import PageParams


async def add(db: AsyncSession, row: MoneyAccount | MoneyMovement) -> None:
    db.add(row)
    await db.flush()


async def get_account(
    db: AsyncSession, company_id: uuid.UUID, account_id: uuid.UUID
) -> MoneyAccount | None:
    account: MoneyAccount | None = await db.scalar(
        select(MoneyAccount).where(
            MoneyAccount.company_id == company_id, MoneyAccount.id == account_id
        )
    )
    return account


async def list_accounts(
    db: AsyncSession, company_id: uuid.UUID, *, kinds: Sequence[AccountKind] | None
) -> Sequence[MoneyAccount]:
    filters: list[ColumnElement[bool]] = [MoneyAccount.company_id == company_id]
    if kinds:
        filters.append(MoneyAccount.kind.in_(kinds))
    rows = await db.scalars(
        select(MoneyAccount).where(*filters).order_by(MoneyAccount.kind, MoneyAccount.name)
    )
    return rows.all()


async def get_movement(
    db: AsyncSession, company_id: uuid.UUID, movement_id: uuid.UUID
) -> MoneyMovement | None:
    movement: MoneyMovement | None = await db.scalar(
        select(MoneyMovement).where(
            MoneyMovement.company_id == company_id, MoneyMovement.id == movement_id
        )
    )
    return movement


_SORTS: dict[str, Any] = {
    "-booked_on": (MoneyMovement.booked_on.desc(), MoneyMovement.id.desc()),
    "booked_on": (MoneyMovement.booked_on, MoneyMovement.id),
    "-amount": (MoneyMovement.amount.desc(), MoneyMovement.id.desc()),
    "amount": (MoneyMovement.amount, MoneyMovement.id),
}


async def list_movements(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None,
    categories: Sequence[MovementCategory] | None,
    uncategorised: bool,
    since: date | None,
    until: date | None,
    estimated: bool | None,
    sort: str,
    page: PageParams,
) -> tuple[Sequence[MoneyMovement], int]:
    filters: list[ColumnElement[bool]] = [MoneyMovement.company_id == company_id]
    if account_id is not None:
        filters.append(MoneyMovement.account_id == account_id)
    if categories:
        filters.append(MoneyMovement.category.in_(categories))
    if uncategorised:
        filters.append(MoneyMovement.category.is_(None))
    if since is not None:
        filters.append(MoneyMovement.booked_on >= since)
    if until is not None:
        filters.append(MoneyMovement.booked_on <= until)
    if estimated is not None:
        filters.append(MoneyMovement.estimated.is_(estimated))
    total = await db.scalar(select(func.count()).select_from(MoneyMovement).where(*filters))
    rows = await db.scalars(
        select(MoneyMovement)
        .where(*filters)
        .order_by(*_SORTS[sort])
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def outgoing(db: AsyncSession, company_id: uuid.UUID, since: date) -> Sequence[MoneyMovement]:
    """Money out of a bank or cash account since the day, as the account's source gave it: no
    estimate, and none already linked to a bill."""
    rows = await db.scalars(
        select(MoneyMovement)
        .join(MoneyAccount, MoneyAccount.id == MoneyMovement.account_id)
        .where(
            MoneyMovement.company_id == company_id,
            MoneyMovement.booked_on >= since,
            MoneyMovement.amount < 0,
            MoneyMovement.estimated.is_(False),
            MoneyMovement.invoice_id.is_(None),
            MoneyAccount.kind.in_([AccountKind.BANK, AccountKind.CASH]),
        )
        .order_by(MoneyMovement.booked_on, MoneyMovement.id)
    )
    return rows.all()


async def upsert_account(
    db: AsyncSession, company_id: uuid.UUID, source: str, values: dict[str, Any]
) -> uuid.UUID:
    """A source's account, by the source's own id: what the source says now replaces what it
    said before. The id it is kept under."""
    row = {"company_id": company_id, "source": source, **values}
    changing = {key: value for key, value in values.items() if key != "source_ref"}
    if values.get("iban"):
        # The same account read through another source stays one account (ADR-0026): the new
        # source takes it over, with the movements it holds.
        held = await db.scalar(
            select(MoneyAccount).where(
                MoneyAccount.company_id == company_id, MoneyAccount.iban == values["iban"]
            )
        )
        if held is not None and (held.source, held.source_ref) != (source, values["source_ref"]):
            await db.execute(
                update(MoneyAccount)
                .where(MoneyAccount.id == held.id)
                .values(source=source, **values)
            )
            return held.id
    result = await db.execute(
        insert(MoneyAccount)
        .values(row)
        .on_conflict_do_update(index_elements=["company_id", "source", "source_ref"], set_=changing)
        .returning(MoneyAccount.id)
    )
    account_id: uuid.UUID = result.scalar_one()
    return account_id


async def upsert_movements(
    db: AsyncSession, company_id: uuid.UUID, source: str, rows: Sequence[dict[str, Any]]
) -> None:
    """A source's movements, by the source's own id. A category the owner set is kept, and a bill
    or a category Tamga linked a movement to (a bank transfer matched to its bill) stays while
    the source gives none."""
    if not rows:
        return
    statement = insert(MoneyMovement).values(
        [{"company_id": company_id, "source": source, **row} for row in rows]
    )
    excluded = statement.excluded
    owners = MoneyMovement.category_by == CategorySetBy.OWNER
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["company_id", "source", "source_ref"],
            set_={
                "account_id": excluded.account_id,
                "booked_on": excluded.booked_on,
                "amount": excluded.amount,
                "currency": excluded.currency,
                "description": excluded.description,
                "counterparty": excluded.counterparty,
                "counterparty_iban": excluded.counterparty_iban,
                "counterparty_tax_id": excluded.counterparty_tax_id,
                "reference": excluded.reference,
                "estimated": excluded.estimated,
                "invoice_id": func.coalesce(excluded.invoice_id, MoneyMovement.invoice_id),
                # The owner's word stays (ADR-0026).
                "category": case(
                    (owners, MoneyMovement.category),
                    (excluded.category.is_(None), MoneyMovement.category),
                    else_=excluded.category,
                ),
                "category_by": case(
                    (owners, MoneyMovement.category_by),
                    (excluded.category.is_(None), MoneyMovement.category_by),
                    else_=excluded.category_by,
                ),
            },
        )
    )


async def delete_others(
    db: AsyncSession, company_id: uuid.UUID, source: str, keep: Sequence[str]
) -> None:
    """A source's movements it no longer gives: an estimate whose invoice is no longer paid."""
    await db.execute(
        delete(MoneyMovement).where(
            MoneyMovement.company_id == company_id,
            MoneyMovement.source == source,
            MoneyMovement.source_ref.not_in(keep),
        )
    )


async def paid_by_banks(
    db: AsyncSession, company_id: uuid.UUID
) -> Sequence[tuple[uuid.UUID, str, Decimal]]:
    """Each movement on a bank or cash account that names an invoice: the invoice, the currency
    and the amount. The vault's test USDC is a mirror, never a payment of the bill."""
    rows = await db.execute(
        select(MoneyMovement.invoice_id, MoneyMovement.currency, MoneyMovement.amount)
        .join(MoneyAccount, MoneyAccount.id == MoneyMovement.account_id)
        .where(
            MoneyMovement.company_id == company_id,
            MoneyMovement.estimated.is_(False),
            MoneyMovement.invoice_id.is_not(None),
            MoneyAccount.kind.in_((AccountKind.BANK, AccountKind.CASH)),
        )
    )
    return [
        (invoice_id, currency, amount)
        for invoice_id, currency, amount in rows.tuples()
        if invoice_id is not None
    ]


async def carry_owner_categories(db: AsyncSession, company_id: uuid.UUID, source: str) -> None:
    """The owner's category of an estimate onto the bank's movements that replace it, where the
    owner has not set one there: the owner's word outlives the estimate (ADR-0026)."""
    estimate = aliased(MoneyMovement)
    await db.execute(
        update(MoneyMovement)
        .where(
            MoneyMovement.company_id == company_id,
            MoneyMovement.estimated.is_(False),
            MoneyMovement.category_by.is_distinct_from(CategorySetBy.OWNER),
            estimate.company_id == company_id,
            estimate.source == source,
            estimate.estimated.is_(True),
            estimate.category_by == CategorySetBy.OWNER,
            estimate.invoice_id == MoneyMovement.invoice_id,
        )
        .values(category=estimate.category, category_by=CategorySetBy.OWNER)
    )


async def mark_link_error(
    db: AsyncSession, company_id: uuid.UUID, source: str, source_ref: str, reason: str
) -> None:
    """The source could not read the account: its balance is stale until it reads again."""
    await db.execute(
        update(MoneyAccount)
        .where(
            MoneyAccount.company_id == company_id,
            MoneyAccount.source == source,
            MoneyAccount.source_ref == source_ref,
        )
        .values(link_error=reason[:300])
    )


async def companies_with_accounts(db: AsyncSession, source: str) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(MoneyAccount.company_id).where(MoneyAccount.source == source).distinct()
    )
    return list(rows)
