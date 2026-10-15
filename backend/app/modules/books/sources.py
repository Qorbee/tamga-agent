"""The ``BooksSource`` interface: one per accounting product, reads only (ADR-0008). A connected
system is never written: a source defines no write call, and has nowhere to put one.

A country pack implements a source for its accounting product and registers a factory here.
The factory returns None when its connection is not configured, so an unconfigured product is
skipped rather than failing every sync.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.parties.models import PartyKind
from app.modules.parties.service import Identifier


class BudgetSpent(Exception):
    """The month's reads are used up: the sync stops where it is and resumes next month."""


class ReadBudget(Protocol):
    async def spend(self) -> None:
        """Counts one read before it is made; raises BudgetSpent when none is left."""
        ...


@dataclass(frozen=True, slots=True)
class PartyCard:
    source_id: str
    name: str
    kind: PartyKind
    identifiers: list[Identifier] = field(default_factory=list)
    # The firm's account balance as the books keep it (its cari), positive when the firm owes the
    # company and negative when the company owes the firm, in ``balance_currency``, as of
    # ``balance_on``. None when the source gives none.
    balance: Decimal | None = None
    balance_currency: str | None = None
    balance_on: date | None = None


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    source_id: str
    direction: InvoiceDirection
    kind: InvoiceKind
    raw: dict[str, Any]
    number: str | None = None
    e_document_id: str | None = None
    party_source_id: str | None = None
    party_name: str | None = None
    # The party's VKN or TCKN, when the record names no card: it finds the card's party.
    party_tax_id: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    payment_date: date | None = None
    currency: str | None = None
    net_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    remaining_amount: Decimal | None = None
    cancelled: bool = False
    status: str | None = None
    description: str | None = None
    # The source's id of the record of the same bill in another list, where the source says.
    same_as_source_id: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    source_id: str
    raw: dict[str, Any]
    number: str | None = None
    e_document_id: str | None = None
    party_source_id: str | None = None
    party_name: str | None = None
    # The party's VKN or TCKN, when the record names no card: it finds the card's party.
    party_tax_id: str | None = None
    issue_date: date | None = None


class BooksSource(Protocol):
    """Reads one company's accounting records. Every method only reads."""

    name: str

    def parties(self) -> AsyncIterator[PartyCard]:
        """Every customer and supplier card."""
        ...

    def invoices(self, start: date, end: date) -> AsyncIterator[InvoiceRecord]:
        """Invoices dated from ``start`` to ``end``, both included."""
        ...

    def dispatches(self, start: date, end: date) -> AsyncIterator[DispatchRecord]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Taxpayer:
    """A tax number as the tax registry knows it: the name its holder is registered under, when
    the registry gives one, and the mail domains of its holder's e-document addresses."""

    tax_id: str
    title: str | None = None
    domains: tuple[str, ...] = ()


@runtime_checkable
class TaxRegistry(Protocol):
    """A source that can also ask the tax registry who holds a tax number, as an e-invoice
    integrator can: it delivers e-invoices by that number. It only reads."""

    async def taxpayer(self, tax_id: str) -> Taxpayer | None:
        """The holder of ``tax_id``, or None when the registry has none on record."""
        ...


class ChangesUnknown(Exception):
    """The source cannot say which of its records changed: the caller reads the open ones again
    instead."""


@runtime_checkable
class ChangeFeed(Protocol):
    """A source that can list the invoices whose record changed since a time, however old the
    invoice: a payment recorded today on last spring's invoice changes it, and a sync that reads
    by date never sees it again. It only reads."""

    def changed_invoices(self, since: datetime) -> AsyncIterator[InvoiceRecord]:
        """Invoices whose record changed on or after ``since``, in the source's own time; raises
        ChangesUnknown, before or while yielding, when the source cannot tell."""
        ...


@runtime_checkable
class InvoiceFiles(Protocol):
    """A source that can hand over an invoice's own file, as its e-invoice system rendered it.
    It only reads."""

    async def invoice_file(self, e_document_id: str) -> tuple[bytes, str] | None:
        """The file of the e-document ``e_document_id`` names, with its media type; None when the
        source has none for it."""
        ...


# (company id, budget) → a source for that company, or None when it is not configured for it.
type SourceFactory = Callable[[uuid.UUID, ReadBudget], BooksSource | None]
# (the record as the source gave it, its direction, its kind) → the invoice it maps to now.
type InvoiceRemap = Callable[[dict[str, Any], InvoiceDirection, InvoiceKind], InvoiceRecord | None]


@dataclass(frozen=True, slots=True)
class RegisteredSource:
    factory: SourceFactory
    # Reads a month Tamga allows itself against this product's quota; read when a sync starts.
    monthly_reads: Callable[[], int]
    # Maps a stored record again, so a field added to the mapping fills without a read.
    remap: InvoiceRemap | None = None


_sources: dict[str, RegisteredSource] = {}


def register(
    name: str,
    factory: SourceFactory,
    *,
    monthly_reads: Callable[[], int],
    remap: InvoiceRemap | None = None,
) -> None:
    _sources[name] = RegisteredSource(factory=factory, monthly_reads=monthly_reads, remap=remap)


def registered() -> dict[str, RegisteredSource]:
    return dict(_sources)
