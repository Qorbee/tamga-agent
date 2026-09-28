"""Facts: typed values read from every item, and the lookups the candidate search is built on
(docs/architecture/09, ADR-0010).

Extractors live where their knowledge does: code in this module and in packs (plates, tax ids,
ETTNs), a model in the agents layer. Each hands its findings to ``record_facts``, which runs in the
caller's transaction and never commits.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.facts import repository
from app.modules.facts.models import Fact
from app.platform.items import ItemKind


@dataclass(frozen=True, slots=True)
class NewFact:
    kind: str
    value: str
    normalised: str
    confidence: float
    extractor: str


async def record_facts(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    item_kind: ItemKind,
    item_id: uuid.UUID,
    facts: Iterable[NewFact],
) -> list[Fact]:
    """Stores each fact once per item; reading the item again returns the same rows."""
    stored = []
    for fact in facts:
        if not fact.normalised or not 0 <= fact.confidence <= 1:
            raise ValueError(f"a {fact.kind} fact needs a normalised value and a 0-1 confidence")
        stored.append(
            await repository.upsert(
                db,
                company_id=company_id,
                item_kind=item_kind,
                item_id=item_id,
                kind=fact.kind[:40],
                value=fact.value,
                normalised=fact.normalised[:300],
                confidence=fact.confidence,
                extractor=fact.extractor[:60],
            )
        )
    return stored


async def facts_of(
    db: AsyncSession, *, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> Sequence[Fact]:
    return await repository.for_item(db, company_id, item_kind, item_id)


async def items_sharing(
    db: AsyncSession, *, company_id: uuid.UUID, kind: str, normalised: str
) -> Sequence[Fact]:
    """Every fact of this kind and value in the company: the strong-key half of the candidate
    search (an order number, an ETTN, a plate)."""
    return await repository.matching(db, company_id, kind, normalised)


async def get_facts(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Fact]:
    return {fact.id: fact for fact in await repository.get_many(db, company_id, ids)}
