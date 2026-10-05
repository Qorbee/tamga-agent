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


async def replace_facts(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    item_kind: ItemKind,
    item_id: uuid.UUID,
    facts: Sequence[NewFact],
) -> bool:
    """An item's facts made what ``facts`` says now, for an item read again from a source that
    corrects it: a number gone from it is no longer its fact. A fact that stays keeps its id, so
    a link citing it still does. Returns whether anything changed. Does not commit."""
    held = await repository.for_item(db, company_id, item_kind, item_id)
    wanted = {(fact.kind, fact.normalised) for fact in facts}
    gone = [fact.id for fact in held if (fact.kind, fact.normalised) not in wanted]
    if gone:
        await repository.forget_ids(db, company_id, gone)
    await record_facts(db, company_id=company_id, item_kind=item_kind, item_id=item_id, facts=facts)
    return bool(gone) or wanted != {(fact.kind, fact.normalised) for fact in held}


async def facts_of(
    db: AsyncSession, *, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> Sequence[Fact]:
    return await repository.for_item(db, company_id, item_kind, item_id)


async def facts_of_items(
    db: AsyncSession, *, company_id: uuid.UUID, item_kind: ItemKind, item_ids: Sequence[uuid.UUID]
) -> Sequence[Fact]:
    """The facts of several items of one kind, in one query."""
    return await repository.for_items(db, company_id, item_kind, item_ids)


async def items_sharing(
    db: AsyncSession, *, company_id: uuid.UUID, kind: str, normalised: str
) -> Sequence[Fact]:
    """Every fact of this kind and value in the company: the strong-key half of the candidate
    search (an order number, an ETTN, a plate)."""
    return await repository.matching(db, company_id, kind, normalised)


async def facts_of_kind(
    db: AsyncSession, *, company_id: uuid.UUID, kind: str, item_kind: ItemKind, limit: int
) -> Sequence[Fact]:
    """The newest ``limit`` facts of one kind read from one kind of item (every tax number in the
    company's mail)."""
    return await repository.of_kind(db, company_id, kind, item_kind, limit)


async def items_sharing_each(
    db: AsyncSession, *, company_id: uuid.UUID, pairs: Sequence[tuple[str, str]], per_pair: int
) -> dict[tuple[str, str], list[Fact]]:
    """``items_sharing`` for several (kind, normalised value) pairs at once: the first
    ``per_pair`` facts of each."""
    found: dict[tuple[str, str], list[Fact]] = {pair: [] for pair in pairs}
    if pairs:
        for fact in await repository.matching_each(db, company_id, pairs, per_pair=per_pair):
            found[(fact.kind, fact.normalised)].append(fact)
    return found


async def forget_item(
    db: AsyncSession, *, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> None:
    """Drops every fact read from an item that is being deleted. Runs in the caller's
    transaction."""
    await repository.forget(db, company_id, item_kind, item_id)


async def get_facts(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Fact]:
    return {fact.id: fact for fact in await repository.get_many(db, company_id, ids)}
