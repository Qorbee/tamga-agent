import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.parties.models import PartyKind

# "source": the parties synced from the books first, then by name. "balance": the most the company
# owes first; "-balance": the most owed to the company first.
PartySort = Literal[
    "source", "name", "-name", "created_at", "-created_at", "balance", "-balance", "open"
]
# Which way a party's balance in the books runs: it owes the company, or the company owes it.
BalanceSide = Literal["receivable", "payable"]


class PartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: PartyKind
    # Whether a person in the company set the kind; a sync never changes it then.
    kind_chosen: bool
    # The accounting system it is synced from, or null for one read from mail or added by hand.
    source: str | None
    # Its account balance in the books (the cari): positive when it owes the company, negative
    # when the company owes it, as of balance_on. Null when the books give none.
    balance: Decimal | None
    balance_currency: str | None
    balance_on: date | None
    created_at: datetime


class PartyChange(BaseModel):
    """What a person in the company says a party is: a person (a sole trader too) or an
    organisation."""

    kind: PartyKind


class PartyPage(BaseModel):
    items: list[PartyOut]
    total: int
    limit: int
    offset: int
