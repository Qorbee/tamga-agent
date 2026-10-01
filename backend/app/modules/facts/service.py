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
    """Stores each fact once per item, in one statement; reading the item again returns the same
    rows."""
    best: dict[tuple[str, str], tuple[str, str, str, float, str]] = {}
    for fact in facts:
        if not fact.normalised or not 0 <= fact.confidence <= 1:
            raise ValueError(f"a {fact.kind} fact needs a normalised value and a 0-1 confidence")
        row = (
            fact.kind[:40],
            fact.value,
            fact.normalised[:300],
            fact.confidence,
            fact.extractor[:60],
        )
        # One statement cannot touch a row twice: the same fact twice keeps its best reading.
        key = (row[0], row[2])
        if key not in best or row[3] > best[key][3]:
            best[key] = row
    stored = await repository.upsert(
        db, company_id=company_id, item_kind=item_kind, item_id=item_id, facts=list(best.values())
    )
    return list(stored)


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
