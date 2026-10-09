import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.money.models import AccountKind, CategorySetBy, MovementCategory

_CURRENCY = r"^[A-Z]{3}$"
_IBAN = r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}$"


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: AccountKind
    name: str
    bank: str | None
    iban: str | None
    currency: str
    # What the source or the owner last said it holds, and when; None where nobody knows.
    balance: Decimal | None
    balance_at: datetime | None
    # "typed" for one the owner keeps, else the source that reads it.
    source: str
    # Why its source could not read it last time: its balance is stale while set.
    link_error: str | None
    created_at: datetime


class AccountPage(BaseModel):
    items: list[AccountOut]
    total: int


class AccountIn(BaseModel):
    """An account the owner keeps by hand: a cash box, or a bank not connected."""

    kind: Literal[AccountKind.BANK, AccountKind.CASH]
    name: str = Field(min_length=1, max_length=120)
    bank: str | None = Field(default=None, max_length=120)
    iban: str | None = Field(default=None, pattern=_IBAN)
    currency: str = Field(pattern=_CURRENCY)
    balance: Decimal | None = Field(default=None, max_digits=16, decimal_places=2)


class AccountChange(BaseModel):
    """What the owner changes on an account they keep: its name, or what it holds now."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    balance: Decimal | None = Field(default=None, max_digits=16, decimal_places=2)


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    source: str
    booked_on: date
    # Positive in, negative out, in the account's currency.
    amount: Decimal
    currency: str
    description: str | None
    counterparty: str | None
    counterparty_iban: str | None
    counterparty_tax_id: str | None
    reference: str | None
    # From the books, dated by the invoice's payment or due day, until a bank's replaces it.
    estimated: bool
    category: MovementCategory | None
    category_by: CategorySetBy | None
    invoice_id: uuid.UUID | None
    created_at: datetime


class MovementPage(BaseModel):
    items: list[MovementOut]
    total: int


# "-booked_on": the latest first.
MovementSort = Literal["-booked_on", "booked_on", "-amount", "amount"]


class MovementIn(BaseModel):
    """One movement the owner says happened: "a partner took 20,000 in cash"."""

    account_id: uuid.UUID
    booked_on: date
    amount: Decimal = Field(max_digits=16, decimal_places=2)
    description: str | None = Field(default=None, max_length=500)
    counterparty: str | None = Field(default=None, max_length=300)
    category: MovementCategory | None = None


class MovementChange(BaseModel):
    """The owner's category for a movement; it stays through every later sync."""

    category: MovementCategory


class Holding(BaseModel):
    """One account's balance, in its own currency and in the one asked for."""

    account_id: uuid.UUID
    balance: Decimal
    currency: str
    # In the currency asked for, at the reference rate, with the rate's day and source; None when
    # no rate stands, and the account is then left out of the total.
    converted: Decimal | None
    rate: Decimal | None
    rate_on: date | None
    rate_source: str | None


class Holdings(BaseModel):
    """What the company holds, account by account, and in total in one currency. Test USDC and
    accounts with no known balance are not money held, so they are not here."""

    currency: str
    total: Decimal
    # Every counted account converted: false when one had no rate and is left out of the total.
    complete: bool
    items: list[Holding]


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """An account as a source gives it, keyed by the source's own id."""

    source_ref: str
    kind: AccountKind
    name: str
    currency: str
    bank: str | None = None
    iban: str | None = None
    balance: Decimal | None = None
    balance_at: datetime | None = None
    # Why the source could not read it; None, as it gives it, clears what was there.
    link_error: str | None = None


@dataclass(frozen=True, slots=True)
class MovementRecord:
    """A movement as a source gives it, on the account its ``account_ref`` names."""

    source_ref: str
    account_ref: str
    booked_on: date
    amount: Decimal
    currency: str
    description: str | None = None
    counterparty: str | None = None
    counterparty_iban: str | None = None
    counterparty_tax_id: str | None = None
    reference: str | None = None
    estimated: bool = False
    # What the source can say for sure; None leaves it to the owner.
    category: MovementCategory | None = None
    invoice_id: uuid.UUID | None = None
