"""The payment run (docs/architecture/05, The payment run): the bills to pay on a day, weighed in
order against what the accounts hold once the reserve keeps its share, so the owner sees which
fit before approving them. Code works out every figure; approving the run is approving each of
its bills, through the same answer as one bill's."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.decisions import repository, service
from app.modules.decisions.models import Decision, DecisionState
from app.modules.decisions.schemas import (
    DecisionAction,
    DecisionOut,
    PaymentRunOut,
    RunApprove,
    RunApproved,
    RunItem,
    RunRefusal,
)
from app.modules.parties import service as parties
from app.modules.policy import service as policies
from app.platform.auth import Actor
from app.platform.errors import Conflict, NotFound
from app.platform.pagination import PageParams

# The decisions read at a time: every page is read, since one page in the repository's order
# could leave out an approved bill or the one due first.
_PAGE: Final = 200
_IN_RUN: Final = (DecisionState.APPROVED, DecisionState.AWAITING_OWNER)


def run_day(today: date, weekday: int | None) -> date:
    """The next pay day from today, today included; today when the owner keeps none."""
    if weekday is None:
        return today
    return today + timedelta(days=(weekday - today.weekday()) % 7)


async def payment_run(db: AsyncSession, actor: Actor, on: date | None) -> PaymentRunOut:
    policy = await policies.current_policy(db, actor.company_id)
    standing = await service._standing(db, actor.company_id, policy)
    today = standing.today
    day = max(on, today) if on is not None else run_day(today, policy.payment_weekday)
    # Up to the next run: a week on with a pay day, the day itself without one.
    through = day + timedelta(days=6 if policy.payment_weekday is not None else 0)

    rows: list[Decision] = []
    while True:
        page, total = await repository.list_decisions(
            db,
            actor.company_id,
            states=[*_IN_RUN, DecisionState.HELD],
            invoice_id=None,
            party_id=None,
            page=PageParams(limit=_PAGE, offset=len(rows)),
        )
        rows.extend(page)
        if not page or len(rows) >= total:
            break
    bills = {
        bill.id: bill
        for bill in await books.get_invoices(db, actor.company_id, [row.invoice_id for row in rows])
    }
    due: list[DecisionOut] = []
    for row in rows:
        bill = bills.get(row.invoice_id)
        if bill is None or (bill.due_date is not None and bill.due_date > through):
            continue
        due.append(DecisionOut.model_validate(row).model_copy(update={"invoice": bill}))

    def order(item: DecisionOut) -> tuple[bool, int, date, uuid.UUID]:
        # What is approved is spent already; then what the company's own rules put first, and
        # last what they put last (ADR-0033); within each, the oldest due first, no day last.
        due_on = item.invoice.due_date if item.invoice else None
        first_or_last = policies.order_of([result.rule for result in item.rules])
        return (
            item.state is not DecisionState.APPROVED,
            first_or_last,
            due_on or date.max,
            item.id,
        )

    in_run = sorted((item for item in due if item.state in _IN_RUN), key=order)
    held = sorted((item for item in due if item.state is DecisionState.HELD), key=order)

    amounts = {
        invoice_id: amount for invoice_id, (_, amount) in standing.dues.items()
    } | standing.approved
    run_bills = {item.invoice_id for item in in_run}
    kept = _kept(standing, run_bills)
    spare = standing.balance - kept if standing.balance is not None and kept is not None else None
    items: list[RunItem] = []
    spent: Decimal | None = Decimal(0)
    for item in in_run:
        amount = amounts.get(item.invoice_id)
        spent = spent + amount if spent is not None and amount is not None else None
        fits = spent <= spare if spent is not None and spare is not None else None
        items.append(RunItem(decision=item, in_policy_currency=amount, fits=fits))
    return PaymentRunOut(
        on=day,
        today=today,
        through=through,
        currency=policy.currency,
        reserve=policy.reserve,
        balance=standing.balance,
        kept=kept,
        spare=spare,
        items=items,
        held=held,
    )


def _kept(standing: service._Standing, run_bills: set[uuid.UUID]) -> Decimal | None:
    """What the reserve keeps for the bills outside the run: those approved, and those due within
    its weeks or overdue, with its floor. With the reserve off, nothing: the balance is spent down
    to zero, never below."""
    rule = standing.policy.reserve
    if not rule.active:
        return Decimal(0)
    horizon = standing.today + timedelta(weeks=rule.cover_weeks)
    outside = [
        amount for invoice_id, amount in standing.approved.items() if invoice_id not in run_bills
    ] + [
        amount
        for invoice_id, (due, amount) in standing.dues.items()
        if invoice_id not in run_bills
        and invoice_id not in standing.approved
        and due is not None
        and due <= horizon
    ]
    if any(amount is None for amount in outside):
        return None
    return sum((amount for amount in outside if amount is not None), rule.floor)


async def approve(db: AsyncSession, actor: Actor, body: RunApprove) -> RunApproved:
    """Approves each bill as the owner would one by one, in the order given; one the rules no
    longer allow, or evaluated again since, is refused with the reason and the rest go on. Each
    approval commits on its own."""
    approved: list[uuid.UUID] = []
    refused: list[RunRefusal] = []
    for item in body.items:
        try:
            await service.act(
                db,
                actor,
                item.decision_id,
                DecisionAction(action="approve", evaluation=item.evaluation),
            )
        except (Conflict, NotFound) as error:
            await db.rollback()
            refused.append(RunRefusal(decision_id=item.decision_id, reason=str(error)))
            continue
        approved.append(item.decision_id)
    return RunApproved(approved=approved, refused=refused)


async def rule_applies(
    db: AsyncSession, actor: Actor, rule: policies.CustomRule
) -> list[DecisionOut]:
    """The bills waiting for the owner or held that a rule of the company's own applies to now:
    what confirming it would put first or last, ask or hold (ADR-0033). An approved bill is the
    executor's, and no rule moves it. It changes nothing."""
    policy = await policies.current_policy(db, actor.company_id)
    standing = await service._standing(db, actor.company_id, policy)
    rows: list[Decision] = []
    while True:
        page, total = await repository.list_decisions(
            db,
            actor.company_id,
            states=[DecisionState.AWAITING_OWNER, DecisionState.HELD],
            invoice_id=None,
            party_id=None,
            page=PageParams(limit=_PAGE, offset=len(rows)),
        )
        rows.extend(page)
        if not page or len(rows) >= total:
            break
    bills = {
        bill.id: bill
        for bill in await books.get_invoices(db, actor.company_id, [row.invoice_id for row in rows])
    }
    firms = await parties.get_parties(db, actor.company_id, list({row.party_id for row in rows}))
    groups = await parties.all_groups(db, actor.company_id)
    asks_client = any(each.attribute == "client_paid" for each in rule.when)
    found: list[DecisionOut] = []
    for row in rows:
        bill = bills.get(row.invoice_id)
        if bill is None:
            continue
        party = firms.get(row.party_id)
        facts = policies.Facts(
            party_id=row.party_id,
            groups=frozenset(group.id for group in groups if row.party_id in group.party_ids),
            known_groups=frozenset(group.id for group in groups),
            party_kind=party.kind.value if party is not None else None,
            issued_on=bill.issue_date,
            due_on=bill.due_date,
            today=standing.today,
            in_policy_currency=_in_policy(row, policy.currency),
            currency=bill.currency,
            method=row.method,
            client_paid=await service.client_paid(db, actor.company_id, bill.id, policy)
            if asks_client
            else None,
        )
        if policies.applies(rule, facts):
            found.append(DecisionOut.model_validate(row).model_copy(update={"invoice": bill}))
    return found


def _in_policy(row: Decision, currency: str) -> Decimal | None:
    if row.currency == currency:
        return row.amount
    return row.amount * row.reference_rate if row.reference_rate is not None else None
