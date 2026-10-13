"""The views the chat shows (ADR-0029): the model chooses one from a closed catalog and the
records it shows, by the filters its resource takes; the client reads every figure from the API.
Nothing here carries an amount, and nothing in a view moves money or changes a record.

A firm is named by the handle a search gave the run (3 for p3), resolved here to the id the
resource filters by, so a view shows only a firm the run was shown. An account is named by words
of its name, bank or currency, and shown only when they fit exactly one of the company's.
"""

from datetime import date
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.assistant.seat import Seat
from app.modules.decisions.models import DecisionState
from app.modules.money import service as money
from app.modules.money.models import MovementCategory
from app.modules.money.schemas import AccountOut
from app.modules.projects.models import ProjectStatus
from app.platform.db import open_session

viewing: FunctionToolset[Seat] = FunctionToolset()

TITLE: Final = 60


class Bills(BaseModel):
    """The company's bills and what Tamga decided about each: to approve, held, paid."""

    view: Literal["decisions"]
    states: list[DecisionState] = Field(default_factory=list, max_length=8)
    # A firm's handle number (3 for p3), from a search.
    firm: int | None = None


class Totals(BaseModel):
    """What invoices come to, month by month by their due day: what clients owe, or what is
    owed to carriers."""

    view: Literal["invoice_totals"]
    direction: Literal["receivable", "payable"]
    # Only what is left to pay on them.
    outstanding: bool = True
    firm: int | None = None
    # How many months, from this one.
    months: int = Field(default=3, ge=1, le=12)


class Outlook(BaseModel):
    """What is due in and out week by week, overdue first, each currency apart."""

    view: Literal["money_outlook"]
    weeks: int = Field(default=4, ge=1, le=8)


class Movements(BaseModel):
    """What moved through the accounts, by category."""

    view: Literal["movements"]
    category: MovementCategory | None = None
    # Words of one account's name or bank, or its currency ("Office EUR"); none, every account.
    account: str | None = Field(default=None, max_length=60)
    since: date | None = None
    until: date | None = None


class Projects(BaseModel):
    """The company's jobs."""

    view: Literal["projects"]
    statuses: list[ProjectStatus] = Field(default_factory=list, max_length=5)
    # Words a job's number, name or parties hold.
    words: str | None = Field(default=None, max_length=80)


class Run(BaseModel):
    """The next payment run: the bills due by it, Tamga's call on each, and the cash against
    the reserve."""

    view: Literal["payment_run"]
    # A later run's day; none, the next.
    on: date | None = None


View = Annotated[Bills | Totals | Outlook | Movements | Projects | Run, Field(discriminator="view")]


@viewing.tool
async def show(context: RunContext[Seat], what: View, title: str = "") -> dict[str, Any] | str:
    """Shows the owner a view under your answer, drawn by the app from the company's records:
    their bills and Tamga's call on each, invoice totals month by month, money due in and out
    week by week, what moved through the accounts, their jobs, or the next payment run. Use it
    when an answer is a list, totals or a trend, and write one or two sentences on what matters
    in it: the view carries the figures, so do not repeat them. A short title (60 characters)
    says what it shows. A firm by its handle number from a search (3 for p3), a project by its
    number."""
    seat = context.deps
    filters: dict[str, Any] = what.model_dump(mode="json", exclude={"view", "firm", "account"})
    firm = getattr(what, "firm", None)
    if firm is not None:
        party_id = seat.look("p", firm)
        if party_id is None:
            return "Use a firm handle that a search returned."
        filters["party_id"] = str(party_id)
    if isinstance(what, Movements) and what.account:
        async with open_session() as db:
            accounts = (await money.list_accounts(db, seat.actor, kinds=None)).items
        fitting = [account for account in accounts if _fits(account, what.account)]
        if len(fitting) != 1:
            return (
                f"{len(fitting)} of the company's {len(accounts)} accounts fit those words: "
                "name one by its bank and currency, or show every account."
            )
        filters["account_id"] = str(fitting[0].id)
    return {"view": what.view, "title": title[:TITLE], "filters": filters}


def _fits(account: AccountOut, words: str) -> bool:
    said = " ".join((account.name, account.bank or "", account.currency)).casefold()
    return all(word in said for word in words.casefold().split())
