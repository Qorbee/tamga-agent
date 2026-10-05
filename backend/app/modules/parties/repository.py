import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parties.models import (
    IdentifierKind,
    IdentifierSource,
    Party,
    PartyIdentifier,
    PartyKind,
)
from app.platform.pagination import PageParams, escape_like


async def add(db: AsyncSession, party: Party) -> Party:
    db.add(party)
    await db.flush()
    return party


async def upsert_from_source(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    source: str,
    source_id: str,
    name: str,
    kind: PartyKind,
    balance: Decimal | None,
    balance_currency: str | None,
    balance_on: date | None,
) -> Party:
    statement = insert(Party).values(
        company_id=company_id,
        source=source,
        source_id=source_id,
        name=name,
        kind=kind,
        balance=balance,
        balance_currency=balance_currency,
        balance_on=balance_on,
    )
    party = await db.scalar(
        statement.on_conflict_do_update(
            index_elements=[Party.company_id, Party.source, Party.source_id],
            set_={
                column: statement.excluded[column]
                for column in ("name", "kind", "balance", "balance_currency", "balance_on")
            },
        )
        .returning(Party)
        # The row may already sit in this session: return the updated values, not the old ones.
        .execution_options(populate_existing=True)
    )
    if party is None:  # an upsert always returns its row
        raise RuntimeError("party upsert returned nothing")
    return party


async def add_identifier(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    party_id: uuid.UUID,
    kind: IdentifierKind,
    value: str,
    normalised: str,
    source: IdentifierSource,
    confirmed: bool,
) -> None:
    """Stores the identifier once per party. Seen again from a confirming source, it becomes
    confirmed; it never goes back to unconfirmed."""
    statement = insert(PartyIdentifier).values(
        company_id=company_id,
        party_id=party_id,
        kind=kind,
        value=value,
        normalised=normalised,
        source=source,
        confirmed=confirmed,
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[
                PartyIdentifier.party_id,
                PartyIdentifier.kind,
                PartyIdentifier.normalised,
            ],
            set_={"confirmed": PartyIdentifier.confirmed | statement.excluded.confirmed},
        )
    )


async def find_by_identifier(
    db: AsyncSession, company_id: uuid.UUID, kind: IdentifierKind, normalised: str
) -> list[tuple[Party, PartyIdentifier]]:
    rows = await db.execute(
        select(Party, PartyIdentifier)
        .join(PartyIdentifier, PartyIdentifier.party_id == Party.id)
        .where(
            Party.company_id == company_id,
            PartyIdentifier.company_id == company_id,
            PartyIdentifier.kind == kind,
            PartyIdentifier.normalised == normalised,
        )
        .order_by(PartyIdentifier.confirmed.desc(), Party.name)
    )
    return [(party, identifier) for party, identifier in rows.tuples()]


async def get_many(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Party]:
    parties = await db.scalars(
        select(Party).where(Party.company_id == company_id, Party.id.in_(ids))
    )
    return parties.all()


async def get_identifiers(
    db: AsyncSession, company_id: uuid.UUID, party_id: uuid.UUID
) -> Sequence[PartyIdentifier]:
    identifiers = await db.scalars(
        select(PartyIdentifier)
        .where(PartyIdentifier.company_id == company_id, PartyIdentifier.party_id == party_id)
        .order_by(PartyIdentifier.kind, PartyIdentifier.created_at)
    )
    return identifiers.all()


async def get_by_source(
    db: AsyncSession, company_id: uuid.UUID, source: str, source_id: str
) -> Party | None:
    party: Party | None = await db.scalar(
        select(Party).where(
            Party.company_id == company_id, Party.source == source, Party.source_id == source_id
        )
    )
    return party


async def list_parties(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    kind: PartyKind | None,
    q: str | None,
    normalised: str | None,
    balance_side: str | None = None,
    order_by: Sequence[ColumnElement[Any]],
    page: PageParams,
) -> tuple[Sequence[Party], int]:
    """The company's parties in the order given. ``q`` matches the name as written, and
    ``normalised`` a name identifier in its compared form, so "ozdemir" finds "ÖZDEMİR".
    ``balance_side`` keeps those that owe the company ("receivable") or that it owes
    ("payable")."""
    filters: list[ColumnElement[bool]] = [Party.company_id == company_id]
    if kind is not None:
        filters.append(Party.kind == kind)
    if balance_side == "receivable":
        filters.append(Party.balance > 0)
    elif balance_side == "payable":
        filters.append(Party.balance < 0)
    if q:
        matches: list[ColumnElement[bool]] = [Party.name.ilike(f"%{escape_like(q)}%", escape="\\")]
        if normalised:
            matches.append(
                exists(
                    select(PartyIdentifier.id).where(
                        PartyIdentifier.company_id == company_id,
                        PartyIdentifier.party_id == Party.id,
                        PartyIdentifier.kind == IdentifierKind.NAME,
                        PartyIdentifier.normalised.like(
                            f"%{escape_like(normalised)}%", escape="\\"
                        ),
                    )
                )
            )
        filters.append(or_(*matches))
    total = await db.scalar(select(func.count()).select_from(Party).where(*filters))
    rows = await db.scalars(
        select(Party)
        .where(*filters)
        .order_by(*order_by, Party.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0
