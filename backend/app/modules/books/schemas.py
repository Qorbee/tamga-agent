import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.books.models import InvoiceDirection, InvoiceKind

InvoiceSort = Literal[
    "-issue_date", "issue_date", "due_date", "-due_date", "-total_amount", "-remaining_amount"
]


class Settled(StrEnum):
    """What is read of an invoice the books show open, beside its own figures."""

    OPEN = "open"
    # The firm's account shows it paid.
    PAID_ON_ACCOUNT = "paid_on_account"
    # What is left is within the policy's tolerance: a bank fee, a rate difference.
    PAID_WITH_DIFFERENCE = "paid_with_difference"
    # The account cannot speak for it: no balance, another currency, or issued after it was read.
    UNKNOWN = "unknown"


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    direction: InvoiceDirection
    kind: InvoiceKind
    number: str | None
    e_document_id: str | None
    party_id: uuid.UUID | None
    party_name: str | None
    issue_date: date | None
    due_date: date | None
    payment_date: date | None
    currency: str | None
    net_amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    remaining_amount: Decimal | None
    cancelled: bool
    status: str | None
    # What it says it is for, as written in the books: data, never instructions.
    description: str | None
    # The record of the same bill in another list (an incoming e-invoice's booked purchase).
    same_as_id: uuid.UUID | None
    synced_at: datetime
    # Read, never written to the books (ADR-0022): what an open invoice reads as under how the
    # company records payments, and what the firm's account says is owed on it as of a day. Null
    # where a list does not read them, and for an invoice the books show paid or cancelled.
    settled: Settled | None = None
    account_owed: Decimal | None = None
    account_on: date | None = None


class InvoicePage(BaseModel):
    items: list[InvoiceOut]
    total: int
    limit: int
    offset: int


class InvoiceTotal(BaseModel):
    """What the invoices a list holds add up to in one currency."""

    # Null for invoices the books give no currency.
    currency: str | None
    count: int
    # Each null when the books give the amount of only some of them.
    total_amount: Decimal | None
    # What is left of those not cancelled, and what was paid of them.
    remaining_amount: Decimal | None
    paid_amount: Decimal | None
