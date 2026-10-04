import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.decisions.models import DecisionState, LogEvent
from app.modules.policy.schemas import Outcome


class RuleResult(BaseModel):
    rule: str
    outcome: Outcome
    reason: str


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    party_id: uuid.UUID
    state: DecisionState
    evaluation: int
    policy_version: int
    amount: Decimal
    currency: str | None
    # How much test USDC one unit of the currency is paid as, and what the amount is paid as,
    # in USDC's smallest units (six decimals); None for a currency with no rate.
    rate: Decimal | None
    usdc_units: int | None
    amount_edited: bool
    rules: list[RuleResult]
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    evaluated_at: datetime


class DecisionPage(BaseModel):
    items: list[DecisionOut]
    total: int


class DecisionAction(BaseModel):
    """The owner's answer to the evaluation it names: one for an evaluation since replaced is
    refused. ``edit`` changes the amount, which is a new proposal: every rule runs again, and it
    comes back to the owner."""

    action: Literal["approve", "hold", "reject", "edit"]
    evaluation: int = Field(ge=1)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def _amount_with_edit(self) -> Self:
        if (self.action == "edit") != (self.amount is not None):
            raise ValueError("an amount is given with edit, and only with it")
        return self


class DecisionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision_id: uuid.UUID
    seq: int
    event: LogEvent
    body: dict[str, Any]
    user_id: uuid.UUID | None
    at: datetime
    previous_hash: str
    hash: str


class DecisionEventPage(BaseModel):
    items: list[DecisionEventOut]
    total: int
