import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class InvoiceDirection(StrEnum):
    # Issued by the company: its client owes it.
    RECEIVABLE = "receivable"
    # Issued to the company: it owes a subcontractor or a supplier.
    PAYABLE = "payable"


class InvoiceKind(StrEnum):
    SALES = "sales"
    PURCHASE = "purchase"
    # An e-fatura that arrived for the company, before or apart from its booking as a purchase.
    INCOMING_E_INVOICE = "incoming_e_invoice"
    EXPENSE = "expense"


class Invoice(CompanyScoped, Base):
    """An invoice as the accounting system holds it, upserted on the system's own record id so
    overlapping sync windows never duplicate one (ADR-0008). ``raw`` keeps the record as read, so a
    better mapping can be applied again without spending the read budget."""

    __tablename__ = "invoices"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "source", "source_id"),
        UniqueConstraint("company_id", "id"),
        # Not unique: the same e-fatura can arrive both as an incoming e-invoice and as the
        # purchase it was booked into, two records of the source. Linking treats them as one.
        Index(None, "company_id", "e_document_id"),
        Index(None, "company_id", "issue_date"),
        ForeignKeyConstraint(["company_id", "party_id"], ["parties.company_id", "parties.id"]),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    source: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(100))
    direction: Mapped[InvoiceDirection] = mapped_column(
        text_enum(InvoiceDirection, "invoices_direction_check")
    )
    kind: Mapped[InvoiceKind] = mapped_column(
        text_enum(InvoiceKind, "invoices_kind_check", length=24)
    )
    number: Mapped[str | None] = mapped_column(String(60))
    # The e-document's ETTN, where there is one.
    e_document_id: Mapped[str | None] = mapped_column(String(64))
    party_id: Mapped[uuid.UUID | None]
    party_name: Mapped[str | None] = mapped_column(String(300))
    issue_date: Mapped[date | None]
    due_date: Mapped[date | None]
    # The date it was paid, where the source records one; a paid invoice is otherwise told by
    # ``remaining_amount``.
    payment_date: Mapped[date | None]
    currency: Mapped[str | None] = mapped_column(String(3))
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    # What is still unpaid, as the books say: zero once the invoice is settled.
    remaining_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    cancelled: Mapped[bool] = mapped_column(default=False)
    # The source's own status text, as read.
    status: Mapped[str | None] = mapped_column(String(60))
    # What the invoice says it is for, as written in the books: a freight line with its route,
    # plates or order number. Data, never instructions.
    description: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Dispatch(CompanyScoped, Base):
    """A dispatch note (e-irsaliye) as the accounting system holds it."""

    __tablename__ = "dispatches"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "source", "source_id"),
        Index(None, "company_id", "e_document_id"),
        ForeignKeyConstraint(["company_id", "party_id"], ["parties.company_id", "parties.id"]),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    source: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(100))
    number: Mapped[str | None] = mapped_column(String(60))
    e_document_id: Mapped[str | None] = mapped_column(String(64))
    party_id: Mapped[uuid.UUID | None]
    party_name: Mapped[str | None] = mapped_column(String(300))
    issue_date: Mapped[date | None]
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BooksCursor(CompanyScoped, Base):
    """How far one kind of record has been read from one source: the next sync starts here."""

    __tablename__ = "books_cursors"
    __table_args__ = (UniqueConstraint("company_id", "source", "resource"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    source: Mapped[str] = mapped_column(String(40))
    resource: Mapped[str] = mapped_column(String(40))
    # Records dated up to and including this day have been read.
    synced_through: Mapped[date]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BooksReads(CompanyScoped, Base):
    """Reads spent against a source's monthly quota, counted before each call is made."""

    __tablename__ = "books_reads"
    __table_args__ = (UniqueConstraint("company_id", "source", "month"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    source: Mapped[str] = mapped_column(String(40))
    # The first day of the month, in UTC: the quota is a calendar month of real time.
    month: Mapped[date]
    reads: Mapped[int] = mapped_column(default=0)
