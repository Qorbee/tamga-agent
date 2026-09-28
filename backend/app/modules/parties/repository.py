import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parties.models import (
    IdentifierKind,
    IdentifierSource,
    Party,
    PartyIdentifier,
    PartyKind,
)


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
) -> Party:
    statement = insert(Party).values(
        company_id=company_id, source=source, source_id=source_id, name=name, kind=kind
    )
    party = await db.scalar(
        statement.on_conflict_do_update(
            index_elements=[Party.company_id, Party.source, Party.source_id],
            set_={"name": statement.excluded.name, "kind": statement.excluded.kind},
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
