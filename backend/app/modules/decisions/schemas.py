import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.books.schemas import InvoiceOut
from app.modules.decisions.models import DecisionState, LogEvent
from app.modules.policy.models import PaymentMethod
from app.modules.policy.schemas import Outcome


class RuleResult(BaseModel):
    rule: str
    outcome: Outcome
    reason: str


# The controller's proposals (ADR-0002): pay the whole amount now, pay part of it now, not now,
# or a question for the owner.
Proposal = Literal["pay", "pay_part", "hold", "ask"]


class AdviceOut(BaseModel):
    """The controller's opinion on one evaluation: it moves nothing. The rules decide what can
    be approved, and the owner approves."""

    evaluation: int
    proposal: Proposal
    # With ``pay_part``: what to pay now, in the bill's currency.
    amount: Decimal | None
    # For the owner, in the company's language.
    reason: str
    model: str


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
    # The official reference rate the amount was compared with the policy's limits at: one unit
    # of the bill's currency in the policy's, the day it was published for, and its source; None
    # when the bill is in the policy's currency or no rate stands (ADR-0028).
    reference_rate: Decimal | None
    reference_rate_on: date | None
    reference_source: str | None
    amount_edited: bool
    # How it is paid, whether the owner chose it, and what leaves the company's account: the
    # currency, the amount expected at the reference rate of the evaluation's day, one unit of
    # the bill's currency in it, that day and the rate's source (ADR-0027). None where no rate
    # stands.
    method: PaymentMethod
    pay_chosen: bool
    pay_currency: str | None
    pay_amount: Decimal | None
    pay_rate: Decimal | None
    pay_rate_on: date | None
    pay_rate_source: str | None
    rules: list[RuleResult]
    # The bill's issue day at the last evaluation.
    issued_on: date | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    evaluated_at: datetime
    # The controller's latest opinion on the evaluation in force; None until one is in.
    advice: AdviceOut | None = None
    # The bill, with ``expand=invoice``.
    invoice: InvoiceOut | None = None


# "-evaluated_at": the latest evaluated first; "-issued_on": the newest bill first.
DecisionSort = Literal["-evaluated_at", "-issued_on", "issued_on"]


class DecisionPage(BaseModel):
    items: list[DecisionOut]
    total: int


class DecisionAction(BaseModel):
    """The owner's answer to the evaluation it names: one for an evaluation since replaced is
    refused. ``edit`` changes the amount and ``pay_by`` the way it is paid, each a new proposal:
    every rule runs again, and it comes back to the owner. ``mark_paid`` says the owner paid it
    in the bank, for what left their account (ADR-0027)."""

    action: Literal["approve", "hold", "reject", "edit", "pay_by", "mark_paid"]
    evaluation: int = Field(ge=1)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    # With pay_by: the method, and the currency it leaves the account in (unchanged if none).
    method: PaymentMethod | None = None
    # With pay_by, or with mark_paid and what was paid, on the day it was paid (today if none).
    pay_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    pay_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    paid_on: date | None = None

    @model_validator(mode="after")
    def _fields_with_their_action(self) -> Self:
        if (self.action == "edit") != (self.amount is not None):
            raise ValueError("an amount is given with edit, and only with it")
        if (self.action == "pay_by") != (self.method is not None):
            raise ValueError("a method is given with pay_by, and only with it")
        paid = self.pay_amount is not None
        if (self.action == "mark_paid") != paid or (paid and self.pay_currency is None):
            raise ValueError("what was paid, and its currency, come with mark_paid, and only it")
        if self.paid_on is not None and self.action != "mark_paid":
            raise ValueError("a day paid comes with mark_paid")
        if self.pay_currency is not None and self.action not in ("pay_by", "mark_paid"):
            raise ValueError("a pay currency comes with pay_by or mark_paid")
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
    # The transaction on the chain's explorer, on an event that names one (a payment settled).
    explorer_url: str | None = None
    # The bill the event's decision is about, with ``expand=invoice``, as the books hold it now;
    # and the firm it was owed to when this event's evaluation was made, which a later correction
    # of the books does not change.
    invoice: InvoiceOut | None = None
    party_name: str | None = None


EventSort = Literal["seq", "-seq"]


class DecisionEventPage(BaseModel):
    items: list[DecisionEventOut]
    total: int


class ChainAnchorOut(BaseModel):
    """The newest anchor on the chain that holds the company's head: the row it anchored, and
    whether the log still has that row as it was."""

    seq: int
    hash: str
    at: datetime | None
    explorer_url: str | None
    # Whether the log still has that row as it was.
    holds: bool
    # Whether the anchor kept here, its root computed again from its heads, is the one the vault
    # recorded on Arc; None when the chain cannot be read now.
    on_arc: bool | None


class DecisionChainOut(BaseModel):
    """The company's decision log as a whole: how many rows it holds, the hash its newest row
    ends on, the first row whose hash does not hold, if any, and its last anchor."""

    length: int
    head: str | None
    broken_at: int | None
    anchor: ChainAnchorOut | None = None
