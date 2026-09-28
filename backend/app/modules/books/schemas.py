import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.modules.books.models import InvoiceDirection, InvoiceKind


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
    synced_at: datetime


class InvoicePage(BaseModel):
    items: list[InvoiceOut]
    total: int
    limit: int
    offset: int
