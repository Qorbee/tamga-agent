import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.settlement.models import PaymentState


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision_id: uuid.UUID
    evaluation: int
    invoice_id: uuid.UUID
    payee: str
    # Test USDC in its smallest units (six decimals).
    usdc_units: int
    payment_key: str
    state: PaymentState
    tx_hash: str | None
    # The transaction on the chain's explorer, once there is one.
    explorer_url: str | None
    failure: str | None
    created_at: datetime
    finished_at: datetime | None


class PaymentPage(BaseModel):
    items: list[PaymentOut]
    total: int
