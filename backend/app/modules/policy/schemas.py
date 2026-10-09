from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.policy.models import PaymentMethod, PaymentsRecorded


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Zero: the defaults, while the owner has set none.
    version: int
    currency: str
    approval_limit: Decimal
    pay_when_paid: bool
    payments_recorded: PaymentsRecorded
    # A percentage of an invoice's total.
    remainder_tolerance: Decimal
    methods: list[PaymentMethod]
    default_method: PaymentMethod
    reserve: Decimal
    # Monday 0; None for any day.
    payment_weekday: int | None
    created_at: datetime | None


class PolicyIn(BaseModel):
    """A new version of the company's policy, replacing the one in force."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    approval_limit: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    pay_when_paid: bool
    payments_recorded: PaymentsRecorded = PaymentsRecorded.ON_ACCOUNT
    remainder_tolerance: Decimal = Field(default=Decimal("2.00"), ge=0, le=20, decimal_places=2)
    # The ways the company pays (ADR-0027); without a vault method, crypto is off.
    methods: list[PaymentMethod] = Field(
        default_factory=lambda: [PaymentMethod.VAULT_USDC], min_length=1
    )
    default_method: PaymentMethod = PaymentMethod.VAULT_USDC
    # What the bank and cash accounts must keep after a payment, in the policy's currency.
    reserve: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=14, decimal_places=2)
    payment_weekday: int | None = Field(default=None, ge=0, le=6)

    @model_validator(mode="after")
    def _default_among_methods(self) -> Self:
        if len(set(self.methods)) != len(self.methods):
            raise ValueError("each method once")
        if self.default_method not in self.methods:
            raise ValueError("the default method is one of the methods")
        return self


class PolicyChange(BaseModel):
    """A new version of the company's policy with only these fields changed from the one in force
    when it is written: a screen that shows one setting never writes back another's stale value."""

    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    approval_limit: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    pay_when_paid: bool | None = None
    payments_recorded: PaymentsRecorded | None = None
    remainder_tolerance: Decimal | None = Field(default=None, ge=0, le=20, decimal_places=2)
    methods: list[PaymentMethod] | None = Field(default=None, min_length=1)
    default_method: PaymentMethod | None = None
    reserve: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    # -1 clears it: any day.
    payment_weekday: int | None = Field(default=None, ge=-1, le=6)


# What the hard rules read and give (rules.py)


class Outcome(StrEnum):
    OK = "ok"
    # The owner decides.
    PROPOSE = "propose"
    HOLD = "hold"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class Result:
    rule: str
    outcome: Outcome
    # What failed, in plain words for the owner; empty when the rule passed.
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Vault:
    """The company on the vault, in USDC's smallest units, read from the chain for this pass."""

    balance: int
    per_payment_cap: int
    left_today: int


@dataclass(frozen=True, slots=True)
class Picture:
    """Everything the rules know of one payable, as code found it."""

    # What would be paid, in the bill's currency, and what the books say is left of it.
    amount: Decimal
    left: Decimal | None
    # What the firm's account in the books says is owed on it, as of ``account_on`` (ADR-0022);
    # None when the account cannot speak for it. Whether what is left is within the policy's
    # remainder tolerance.
    owed_on_account: Decimal | None
    account_on: date | None
    within_tolerance: bool
    cancelled: bool
    # The books name the party the bill is from.
    party: bool
    currency: str | None
    # The test USDC the amount is paid as, in USDC's smallest units; None when a currency has no
    # mirror rate (ADR-0019).
    usdc_units: int | None
    # The amount in the policy's currency at the official reference rate; None when no rate
    # stands (ADR-0028).
    in_policy_currency: Decimal | None
    # The projects that hold the bill as evidence (ADR-0010).
    projects: int
    # Each truck the bill pays for, with whether its delivery is proven (ADR-0021, decision 4);
    # None where no pack says what delivering means.
    trucks: tuple[bool, ...] | None
    # The client invoices of those projects, and how many of them still have something left.
    client_invoices: int
    client_unpaid: int
    # The vault has paid this bill's key, or another bill with the same number or e-document id
    # was approved or paid.
    paid_on_chain: bool
    paid_as_another: bool
    # The company is on the vault, and when (Unix time) its payee for this party may be paid.
    vault: Vault | None
    payee: bool
    payee_active_from: int | None
    now: int
