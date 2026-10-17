"""The figures a view in the chat is answered with, kept (ADR-0035): code reads each view's
figures through the services its live component's endpoints call, with the same parameters, and
keeps them, so the chat draws a past answer as it was given. A brief's card keeps its money the
same way. A view's reader here and its component in the web app change together."""

import contextlib
import uuid
from datetime import date, timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant.models import ViewSnapshot
from app.modules.assistant.schemas import SnapshotData, ViewSnapshotOut
from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection
from app.modules.companies import service as companies
from app.modules.decisions import runs
from app.modules.decisions import service as decisions
from app.modules.decisions.models import DecisionState
from app.modules.money import service as money
from app.modules.money.models import MovementCategory
from app.modules.money.schemas import MovementPage
from app.modules.parties import service as parties
from app.modules.policy import service as policy
from app.modules.projects import service as projects
from app.modules.projects.models import ProjectStatus
from app.platform import clock
from app.platform.auth import Actor
from app.platform.errors import NotFound
from app.platform.pagination import PageParams

# As the web app's views list and add up (frontend `features/assistant/views.tsx`).
LISTED: Final = 8
_PAGE: Final = 200
_SUMMED: Final = 2000
GLANCE: Final = "glance"


async def take(
    db: AsyncSession,
    actor: Actor,
    *,
    view: str,
    filters: dict[str, Any],
    chat_id: uuid.UUID | None,
    tool_call_id: str | None = None,
) -> uuid.UUID:
    """Reads the view's figures now and keeps them; the snapshot's id. Does not commit."""
    data = await _read(db, actor, view, filters)
    snapshot = ViewSnapshot(
        company_id=actor.company_id,
        chat_id=chat_id,
        tool_call_id=tool_call_id,
        view=view,
        filters=filters,
        data=data.model_dump(mode="json"),
    )
    db.add(snapshot)
    await db.flush()
    return snapshot.id


async def get(db: AsyncSession, actor: Actor, snapshot_id: uuid.UUID) -> ViewSnapshotOut:
    snapshot = await db.get(ViewSnapshot, snapshot_id)
    if snapshot is None or snapshot.company_id != actor.company_id:
        raise NotFound("No such view")
    return ViewSnapshotOut.model_validate(snapshot)


async def _read(db: AsyncSession, actor: Actor, view: str, filters: dict[str, Any]) -> SnapshotData:
    company = (await companies.get_companies(db, [actor.company_id]))[actor.company_id]
    today = clock.now(company).date()
    data = SnapshotData(today=today)
    party_id = _id(filters.get("party_id"))
    if party_id is not None:
        with contextlib.suppress(NotFound):
            data.party = await parties.get_party(db, actor, party_id)
    if view == "decisions":
        data.decisions = await decisions.list_decisions(
            db,
            actor,
            states=[DecisionState(state) for state in filters.get("states") or []] or None,
            invoice_id=None,
            party_id=party_id,
            with_invoices=True,
            sort="-issued_on",
            page=PageParams(limit=LISTED, offset=0),
        )
    elif view == "invoice_totals":
        direction = filters.get("direction")
        sides = [direction] if direction in ("receivable", "payable") else ["receivable", "payable"]
        months = min(12, max(1, int(filters.get("months") or 3)))
        cutoffs = [_first_of_month(today, ahead) for ahead in range(months + 1)]
        outstanding = filters.get("outstanding") is not False
        await _totals(db, actor, data, sides, cutoffs, outstanding=outstanding, party_id=party_id)
    elif view == "money_outlook":
        weeks = min(8, max(1, int(filters.get("weeks") or 4)))
        cutoffs = [today + timedelta(days=7 * week) for week in range(weeks + 1)]
        await _totals(db, actor, data, ["receivable", "payable"], cutoffs, outstanding=True)
    elif view == "movements":
        account_id = _id(filters.get("account_id"))
        data.movements = await _movements(db, actor, filters, account_id)
        if account_id is not None:
            data.accounts = await money.list_accounts(db, actor, kinds=None)
    elif view == "projects":
        data.projects = await projects.list_projects(
            db,
            actor,
            statuses=[ProjectStatus(status) for status in filters.get("statuses") or []],
            q=filters.get("words") or None,
            suggested=None,
            sort="-created_at",
            page=PageParams(limit=LISTED, offset=0),
        )
    elif view == "payment_run":
        on = filters.get("on")
        data.run = await runs.payment_run(db, actor, date.fromisoformat(on) if on else None)
    elif view == GLANCE:
        data.currency = (await policy.current_policy(db, actor.company_id)).currency
        data.run = await runs.payment_run(db, actor, None)
        data.holdings = await money.holdings(db, actor, data.currency)
        # The run's last day counts: the books' cutoff is strict, so it is the day after.
        cutoff = data.run.through + timedelta(days=1)
        await _totals(db, actor, data, ["receivable", "payable"], [cutoff], outstanding=True)
    return data


async def _totals(
    db: AsyncSession,
    actor: Actor,
    data: SnapshotData,
    sides: list[str],
    cutoffs: list[date],
    *,
    outstanding: bool,
    party_id: uuid.UUID | None = None,
) -> None:
    for side in sides:
        for cutoff in cutoffs:
            data.totals[f"{side}|{cutoff.isoformat()}"] = await books.invoice_totals(
                db,
                actor,
                books.InvoiceFilter(
                    direction=InvoiceDirection(side),
                    outstanding=outstanding,
                    party_id=party_id,
                    due_before=cutoff,
                ),
            )


async def _movements(
    db: AsyncSession, actor: Actor, filters: dict[str, Any], account_id: uuid.UUID | None
) -> MovementPage:
    """Every page up to what a view adds up, so each category's sum holds all it matched."""
    category = filters.get("category")
    since, until = filters.get("since"), filters.get("until")

    async def page(offset: int) -> MovementPage:
        return await money.list_movements(
            db,
            actor,
            account_id=account_id,
            categories=[MovementCategory(category)] if category else None,
            uncategorised=False,
            since=date.fromisoformat(since) if since else None,
            until=date.fromisoformat(until) if until else None,
            estimated=None,
            sort="-booked_on",
            page=PageParams(limit=_PAGE, offset=offset),
        )

    first = await page(0)
    items = list(first.items)
    while len(items) < min(first.total, _SUMMED):
        more = await page(len(items))
        if not more.items:
            break
        items.extend(more.items)
    return MovementPage(items=items, total=first.total)


def _first_of_month(day: date, ahead: int) -> date:
    months = day.year * 12 + day.month - 1 + ahead
    return date(months // 12, months % 12 + 1, 1)


def _id(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None
