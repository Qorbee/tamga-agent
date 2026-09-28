import uuid
from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.facts.models import Fact
from app.platform.items import ItemKind


async def upsert(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    item_kind: ItemKind,
    item_id: uuid.UUID,
    kind: str,
    value: str,
    normalised: str,
    confidence: float,
    extractor: str,
) -> Fact:
    """One row per (item, kind, normalised value). Read again, it keeps the higher confidence and
    the extractor that gave it."""
    statement = insert(Fact).values(
        company_id=company_id,
        item_kind=item_kind,
        item_id=item_id,
        kind=kind,
        value=value,
        normalised=normalised,
        confidence=confidence,
        extractor=extractor,
    )
    higher = statement.excluded.confidence > Fact.confidence
    fact = await db.scalar(
        statement.on_conflict_do_update(
            index_elements=[
                Fact.company_id,
                Fact.item_kind,
                Fact.item_id,
                Fact.kind,
                Fact.normalised,
            ],
            set_={
                "confidence": func.greatest(Fact.confidence, statement.excluded.confidence),
                "extractor": case((higher, statement.excluded.extractor), else_=Fact.extractor),
            },
        )
        .returning(Fact)
        .execution_options(populate_existing=True)
    )
    if fact is None:  # an upsert always returns its row
        raise RuntimeError("fact upsert returned nothing")
    return fact


async def for_item(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> Sequence[Fact]:
    facts = await db.scalars(
        select(Fact)
        .where(Fact.company_id == company_id, Fact.item_kind == item_kind, Fact.item_id == item_id)
        .order_by(Fact.kind, Fact.normalised)
    )
    return facts.all()


async def matching(
    db: AsyncSession, company_id: uuid.UUID, kind: str, normalised: str
) -> Sequence[Fact]:
    facts = await db.scalars(
        select(Fact)
        .where(Fact.company_id == company_id, Fact.kind == kind, Fact.normalised == normalised)
        .order_by(Fact.created_at)
    )
    return facts.all()


async def get_many(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Fact]:
    facts = await db.scalars(select(Fact).where(Fact.company_id == company_id, Fact.id.in_(ids)))
    return facts.all()
