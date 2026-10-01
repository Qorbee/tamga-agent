import uuid
from collections.abc import Sequence

from sqlalchemy import case, delete, func, select
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
    facts: Sequence[tuple[str, str, str, float, str]],
) -> Sequence[Fact]:
    """One row per (item, kind, normalised value), for all of an item's facts in one statement;
    each is ``(kind, value, normalised, confidence, extractor)``, at most once per kind and value.
    Read again, a fact keeps the higher confidence and the extractor that gave it."""
    if not facts:
        return []
    statement = insert(Fact).values(
        [
            {
                "id": uuid.uuid7(),
                "company_id": company_id,
                "item_kind": item_kind,
                "item_id": item_id,
                "kind": kind,
                "value": value,
                "normalised": normalised,
                "confidence": confidence,
                "extractor": extractor,
            }
            for kind, value, normalised, confidence, extractor in facts
        ]
    )
    higher = statement.excluded.confidence > Fact.confidence
    rows = await db.scalars(
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
    return rows.all()


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


async def forget(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> None:
    await db.execute(
        delete(Fact).where(
            Fact.company_id == company_id, Fact.item_kind == item_kind, Fact.item_id == item_id
        )
    )
