import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.modules.policy.models import PaymentMethod
from app.modules.settlement.models import PaymentKind, PaymentState


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision_id: uuid.UUID
    evaluation: int
    invoice_id: uuid.UUID
    kind: PaymentKind
    method: PaymentMethod
    # What left the company's account, in its own currency, and the day.
    pay_currency: str | None
    pay_amount: Decimal | None
    paid_on: date | None
    # A bank transfer's proof: the movement matched to it, or the owner who marked it paid.
    movement_id: uuid.UUID | None
    marked_by: uuid.UUID | None
    # The vault's, for a vault method or a mirror: the payee's wallet, the test USDC in its
    # smallest units (six decimals), and the bill's key.
    payee: str | None
    usdc_units: int | None
    payment_key: str | None
    state: PaymentState
    tx_hash: str | None
    # The transaction on the chain's explorer, once there is one.
    explorer_url: str | None
    failure: str | None
    created_at: datetime
    finished_at: datetime | None
    # Undone by the owner: not paid after all, kept for the record.
    voided_at: datetime | None


class PaymentPage(BaseModel):
    items: list[PaymentOut]
    total: int
