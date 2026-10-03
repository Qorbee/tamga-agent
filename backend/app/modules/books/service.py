"""Books: the company's accounting records, read from its accounting system through a
``BooksSource`` (ADR-0008), never written back.

A sync reads the customer and supplier cards (the identity graph's backbone, confirmed parties),
then invoices and dispatches in date windows from where the last sync stopped. Each record is
upserted on the source's own id, so overlapping windows never duplicate one. Every call is counted
against a monthly read budget before it is made; when the budget is spent the sync keeps what it
read and stops, and the next month continues from its cursor.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.books import repository, sources
from app.modules.books.models import InvoiceDirection
from app.modules.books.schemas import InvoiceOut, InvoicePage
from app.modules.books.sources import (
    BooksSource,
    BudgetSpent,
    DispatchRecord,
    InvoiceRecord,
    PartyCard,
)
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind
from app.modules.parties.service import Identifier
from app.platform import clock
from app.platform.auth import Actor
from app.platform.db import open_session
from app.platform.errors import NotFound
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

# Records are read in windows this long, and the cursor moves after each.
WINDOW: Final = timedelta(days=31)
# Each sync reads back this far behind its cursor: an invoice paid or corrected since the last
# sync is read again.
OVERLAP: Final = timedelta(days=7)
# Cards are read again at most this often.
PARTIES_EVERY: Final = timedelta(days=1)


class DbReadBudget:
    """A source's monthly reads, counted in the database in their own transaction: a read spent
    stays spent even when the sync that spent it rolls back."""

    def __init__(self, company_id: uuid.UUID, source: str, monthly_reads: int) -> None:
        self._company_id = company_id
        self._source = source
        self._limit = monthly_reads

    async def spend(self) -> None:
        month = clock.utcnow().date().replace(day=1)
        async with open_session() as db:
            reads = await repository.spend_read(
                db, self._company_id, self._source, month, self._limit
            )
            await db.commit()
        if reads is None:
            raise BudgetSpent(f"{self._source}: {self._limit} reads spent this month")


@dataclass
class SyncResult:
    parties: int = 0
    invoices: int = 0
    dispatches: int = 0
    # True when the read budget ran out before the sync reached today.
    budget_spent: bool = False
    skipped: list[str] = field(default_factory=list)


async def sync_pilot_books() -> SyncResult:
    """Every configured books source of the pilot company (ADR-0008). A replay company is fed from
    stored history, never from the live system."""
    company_id = get_settings().pilot_company_id
    result = SyncResult()
    if company_id is None:
        return result
    for name, registered in sources.registered().items():
        budget = DbReadBudget(company_id, name, registered.monthly_reads())
        source = registered.factory(company_id, budget)
        if source is None:
            result.skipped.append(name)
            continue
        try:
            one = await sync_source(company_id, source)
        finally:
            await source.close()
        result.parties += one.parties
        result.invoices += one.invoices
        result.dispatches += one.dispatches
        result.budget_spent = result.budget_spent or one.budget_spent
    return result


async def sync_source(company_id: uuid.UUID, source: BooksSource) -> SyncResult:
    result = SyncResult()
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        if company is None:
            raise RuntimeError("the books are read into a company that does not exist")
        if company.mode is CompanyMode.REPLAY:
            result.skipped.append(source.name)
            return result
        today = clock.now(company).date()
        try:
            await _sync_parties(db, company_id, source, today, result)
            await _sync_windows(db, company_id, source, "invoices", today, result)
            await _sync_windows(db, company_id, source, "dispatches", today, result)
        except BudgetSpent as exc:
            # What was read before the budget ran out is kept; the cursor shows how far it got.
            await db.commit()
            logger.warning("books sync stopped: %s", exc)
            result.budget_spent = True
    return result


async def store_records(
    company_id: uuid.UUID,
    source: str,
    cards: Sequence[PartyCard],
    invoices: Sequence[InvoiceRecord],
) -> None:
    """Stores a fixed set of records as if ``source`` had given them: a sandbox's scenario, which
    has no dates to page through and must not depend on how far back the pilot's sync reads."""
    async with open_session() as db:
        for card in cards:
            await parties.sync_party(
                db,
                company_id=company_id,
                source=source,
                source_id=card.source_id,
                name=card.name,
                kind=card.kind,
                identifiers=card.identifiers,
            )
        for invoice in invoices:
            await _store_invoice(db, company_id, source, invoice)
        await db.commit()


async def _sync_parties(
    db: AsyncSession, company_id: uuid.UUID, source: BooksSource, today: date, result: SyncResult
) -> None:
    synced = await repository.get_cursor(db, company_id, source.name, "parties")
    if synced is not None and today - synced < PARTIES_EVERY:
        return
    async for card in source.parties():
        await parties.sync_party(
            db,
            company_id=company_id,
            source=source.name,
            source_id=card.source_id,
            name=card.name,
            kind=card.kind,
            identifiers=card.identifiers,
        )
        result.parties += 1
    await repository.save_cursor(db, company_id, source.name, "parties", today)
    await db.commit()


async def _sync_windows(
    db: AsyncSession,
    company_id: uuid.UUID,
    source: BooksSource,
    resource: str,
    today: date,
    result: SyncResult,
) -> None:
    synced = await repository.get_cursor(db, company_id, source.name, resource)
    start = (
        synced - OVERLAP
        if synced is not None
        else today - timedelta(days=get_settings().books_backfill_days)
    )
    while start <= today:
        end = min(start + WINDOW - timedelta(days=1), today)
        if resource == "invoices":
            async for invoice in source.invoices(start, end):
                await _store_invoice(db, company_id, source.name, invoice)
                result.invoices += 1
        else:
            async for dispatch in source.dispatches(start, end):
                await _store_dispatch(db, company_id, source.name, dispatch)
                result.dispatches += 1
        await repository.save_cursor(db, company_id, source.name, resource, end)
        await db.commit()
        start = end + timedelta(days=1)


async def _store_invoice(
    db: AsyncSession,
    company_id: uuid.UUID,
    source: str,
    record: InvoiceRecord,
    *,
    read: bool = True,
) -> None:
    party_id = await _party_id(db, company_id, source, record.party_source_id, record.party_tax_id)
    await repository.upsert_invoice(
        db,
        {
            "company_id": company_id,
            "source": source,
            "source_id": record.source_id[:100],
            "direction": record.direction,
            "kind": record.kind,
            "number": _cut(record.number, 60),
            "e_document_id": _cut(record.e_document_id, 64),
            "party_id": party_id,
            "party_name": _cut(record.party_name, 300),
            "issue_date": record.issue_date,
            "due_date": record.due_date,
            "payment_date": record.payment_date,
            "currency": record.currency,
            "net_amount": record.net_amount,
            "tax_amount": record.tax_amount,
            "total_amount": record.total_amount,
            "remaining_amount": record.remaining_amount,
            "cancelled": record.cancelled,
            "status": _cut(record.status, 60),
            "description": record.description,
            "raw": record.raw,
        },
        read=read,
    )


async def _store_dispatch(
    db: AsyncSession, company_id: uuid.UUID, source: str, record: DispatchRecord
) -> None:
    party_id = await _party_id(db, company_id, source, record.party_source_id, record.party_tax_id)
    await repository.upsert_dispatch(
        db,
        {
            "company_id": company_id,
            "source": source,
            "source_id": record.source_id[:100],
            "number": _cut(record.number, 60),
            "e_document_id": _cut(record.e_document_id, 64),
            "party_id": party_id,
            "party_name": _cut(record.party_name, 300),
            "issue_date": record.issue_date,
            "raw": record.raw,
        },
    )


async def _party_id(
    db: AsyncSession,
    company_id: uuid.UUID,
    source: str,
    card_id: str | None,
    tax_id: str | None,
) -> uuid.UUID | None:
    """The card's party, else the one confirmed party with this tax id. Never a guess: two
    confirmed matches, or only unconfirmed ones, leave the record without a party."""
    if card_id is not None:
        party = await parties.get_synced_party(db, company_id, source, card_id)
        if party is not None:
            return party.id
    if tax_id is not None:
        matches = await parties.find_parties(
            db, company_id=company_id, identifier=Identifier(IdentifierKind.TAX_ID, tax_id)
        )
        confirmed = [match for match in matches if match.confirmed]
        if len(confirmed) == 1:
            return confirmed[0].party.id
    return None


def _cut(value: str | None, length: int) -> str | None:
    return value[:length] if value is not None else None


# Reading what was synced


async def payable_parties(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    """The parties the company owes bills to, as its books hold them: whom it may pay."""
    return await repository.payable_parties(db, company_id)


async def invoice_exists(db: AsyncSession, company_id: uuid.UUID, invoice_id: uuid.UUID) -> bool:
    return await repository.invoice_exists(db, company_id, invoice_id)


async def list_invoices(
    db: AsyncSession,
    actor: Actor,
    *,
    direction: InvoiceDirection | None,
    party_id: uuid.UUID | None,
    q: str | None,
    page: PageParams,
) -> InvoicePage:
    rows, total = await repository.list_invoices(
        db,
        actor.company_id,
        direction=direction,
        party_id=party_id,
        q=q.strip() if q else None,
        page=page,
    )
    return InvoicePage(
        items=[InvoiceOut.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def find_invoices(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None,
    since: date | None,
    until: date | None,
    limit: int,
) -> list[InvoiceOut]:
    """Invoices by a part of their number, their party's name or their e-document id, issued in
    the span, newest first."""
    rows = await repository.find_invoices(
        db, company_id, q=q.strip() if q else None, since=since, until=until, limit=limit
    )
    return [InvoiceOut.model_validate(row) for row in rows]


async def get_invoices(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[InvoiceOut]:
    rows = await repository.get_invoices(db, company_id, ids)
    return [InvoiceOut.model_validate(row) for row in rows]


async def get_invoice(db: AsyncSession, actor: Actor, invoice_id: uuid.UUID) -> InvoiceOut:
    found = await get_invoices(db, actor.company_id, [invoice_id])
    if not found:
        raise NotFound("No such invoice")
    return found[0]


async def remap_invoices(db: AsyncSession, company_id: uuid.UUID) -> int:
    """Maps every stored invoice again from the record its source gave, so a field the mapping
    has learned fills without reading the source again. Returns how many changed; does not
    commit."""
    changed = 0
    for name, registered in sources.registered().items():
        if registered.remap is None:
            continue
        for invoice in await repository.invoices_of_source(db, company_id, name):
            record = registered.remap(invoice.raw, invoice.direction, invoice.kind)
            if record is None or record.source_id != invoice.source_id:
                continue
            before = InvoiceOut.model_validate(invoice).model_dump(exclude={"synced_at"})
            await _store_invoice(db, company_id, name, record, read=False)
            await db.refresh(invoice)
            if InvoiceOut.model_validate(invoice).model_dump(exclude={"synced_at"}) != before:
                changed += 1
    return changed


async def invoices_named(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    numbers: Sequence[str],
    e_document_ids: Sequence[str],
) -> list[InvoiceOut]:
    """The invoices in the books that a text names: by number as facts compare one (letters and
    digits, upper case), or by e-document id (lower case)."""
    if not numbers and not e_document_ids:
        return []
    rows = await repository.invoices_named(
        db, company_id, numbers=numbers, e_document_ids=e_document_ids
    )
    return [InvoiceOut.model_validate(row) for row in rows]
