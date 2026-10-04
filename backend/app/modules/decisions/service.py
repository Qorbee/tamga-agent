"""Decisions (ADR-0002, [05](../../../../docs/architecture/05-decisions-and-settlement.md)): what
Tamga decides about each bill the company has to pay, and its history.

Each payable is evaluated again whenever what code knows of it changed: its picture is built from
the books, the confirmed links, what a sector pack says of its delivery and the vault on the
chain; the hard rules (``policy``) give the outcome; and an evaluation that differs from the last
one is a new proposal, logged. The owner answers the evaluation in force (approve, hold, reject,
or a new amount, which is evaluated again). No model takes part, and nothing here moves money:
the executor pays an approved decision (``settlement``).

The log is one hash chain per company: each row holds the hash of its content and of the row
before it.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection
from app.modules.books.schemas import InvoiceOut
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.decisions import repository
from app.modules.decisions.models import Decision, DecisionEvent, DecisionState, LogEvent
from app.modules.decisions.schemas import (
    DecisionAction,
    DecisionChainOut,
    DecisionEventOut,
    DecisionEventPage,
    DecisionOut,
    DecisionPage,
)
from app.modules.parties import service as parties
from app.modules.policy import service as policies
from app.modules.policy.schemas import Outcome, Picture, PolicyOut, Vault
from app.modules.projects import service as projects
from app.modules.settlement import service as settlement
from app.platform import chain, events
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import Conflict, NotFound
from app.platform.items import ItemKind
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

# The first row of a company's chain follows this.
GENESIS: Final = "0" * 64

# For a bill: each truck it pays for, and whether its delivery is proven.
DeliveryCheck = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[list[bool]]]
_delivery: list[DeliveryCheck] = []

_STATES: Final = {
    Outcome.REJECT: DecisionState.REJECTED,
    Outcome.HOLD: DecisionState.HELD,
    Outcome.PROPOSE: DecisionState.AWAITING_OWNER,
    # No auto-pay rule exists yet, so a bill every rule passes waits for the owner too.
    Outcome.OK: DecisionState.AWAITING_OWNER,
}


def register_delivery(check: DeliveryCheck) -> None:
    """A sector pack says what delivering means for a bill. With none, the proof rule passes."""
    if _delivery and _delivery[0] is not check:
        raise ValueError("a delivery check is already registered")
    _delivery[:] = [check]


def payment_key(invoice_id: uuid.UUID) -> bytes:
    """The bill's key on the vault, which pays a key once: one payment a bill, for now."""
    return chain.payment_key(str(invoice_id), "final")


# Evaluating


@dataclass(slots=True)
class _OnChain:
    """What the vault says this pass, read before any transaction opens."""

    vault: Vault | None = None
    payees: dict[uuid.UUID, str] = field(default_factory=dict)
    active_from: dict[str, int | None] = field(default_factory=dict)
    paid: set[uuid.UUID] = field(default_factory=set)


async def evaluate_pending() -> int:
    """Evaluates every company's payables, and the bills of decisions no owner has answered yet.
    Returns how many decisions were made or moved. A company whose chain cannot be read waits
    for the next pass, rather than have every bill held for it."""
    async with open_session() as db:
        company_ids = set(await books.companies_with_payables(db))
        company_ids |= set(await repository.companies_with_movable(db))
    moved = 0
    for company_id in sorted(company_ids):
        try:
            moved += await evaluate_company(company_id)
        except Exception as error:  # one company that fails never stops the others
            logger.warning(
                "evaluating the payables of %s failed: %s", company_id, type(error).__name__
            )
    return moved


async def evaluate_company(company_id: uuid.UUID) -> int:
    """One company's pass, each bill in a transaction of its own. A replay company's decisions
    are made at its own clock's time, not here (ADR-0009)."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        if company is None or company.mode is CompanyMode.REPLAY:
            return 0
        bills = {bill.id: bill for bill in await books.open_payables(db, company_id)}
        # A bill no longer open whose decision still waits is evaluated too: paid or cancelled
        # in the books meanwhile, it is held, not left waiting for an approval.
        missing = [i for i in await repository.movable_invoices(db, company_id) if i not in bills]
        for bill in await books.get_invoices(db, company_id, missing):
            bills[bill.id] = bill
    if not bills:
        return 0
    seen = await _on_chain(company_id, list(bills.values()))
    moved = 0
    for bill in bills.values():
        async with open_session() as db:
            await repository.lock_decisions(db, company_id)
            decision = await repository.for_invoice(db, company_id, bill.id)
            if decision is not None and not _movable(decision):
                continue
            if await _decide(db, company_id, bill, decision, seen):
                moved += 1
            await db.commit()
    return moved


def _movable(decision: Decision) -> bool:
    return decision.decided_by is None and decision.state not in _EXECUTORS


# Approved, a decision is the executor's; paid, it is done.
_EXECUTORS: Final = (DecisionState.APPROVED, DecisionState.SETTLED)


async def _on_chain(company_id: uuid.UUID, bills: Sequence[InvoiceOut]) -> _OnChain:
    async with open_session() as db:
        opened = await settlement.account_open(db, company_id)
        payees = await settlement.payee_addresses(
            db, company_id, sorted({bill.party_id for bill in bills if bill.party_id})
        )
    if not opened:
        return _OnChain(payees=payees)
    state = await chain.company_state(company_id)
    if state is None:
        return _OnChain(payees=payees)
    # Asked together: a company with many bills waits for the slowest answer, not their sum.
    addresses = sorted(set(payees.values()))
    active_from, paid = await asyncio.gather(
        asyncio.gather(*(chain.payee_active_from(company_id, a) for a in addresses)),
        asyncio.gather(*(chain.is_paid(company_id, payment_key(bill.id)) for bill in bills)),
    )
    return _OnChain(
        vault=Vault(
            balance=state.balance,
            per_payment_cap=state.per_payment_cap,
            left_today=state.left_today,
        ),
        payees=payees,
        active_from=dict(zip(addresses, active_from, strict=True)),
        paid={bill.id for bill, was in zip(bills, paid, strict=True) if was},
    )


async def _decide(
    db: AsyncSession,
    company_id: uuid.UUID,
    bill: InvoiceOut,
    decision: Decision | None,
    seen: _OnChain,
    *,
    force: bool = False,
) -> bool:
    """Evaluates one bill and keeps the result when it differs from the last evaluation (or
    ``force``, after the owner's edit): a new evaluation number, the state the rules give, and a
    ``proposed`` row in the log. Returns whether it did. Under the caller's lock; no commit."""
    policy = await policies.current_policy(db, company_id)
    amount = (
        decision.amount
        if decision is not None and decision.amount_edited
        else bill.remaining_amount or Decimal("0.00")
    )
    picture = await _picture(db, company_id, bill, amount, policy, seen)
    outcome, results = policies.evaluate(picture, policy)
    # What the rules read, but the time, and what they gave: a timelock that runs out changes
    # the result, so the decision moves then. Whom it pays is part of it: a bill the books move
    # to another party is a new proposal, never an approval of the old one.
    read = {
        "picture": _jsonable(asdict(picture) | {"now": None}),
        "policy": policy.version,
        "party": str(bill.party_id),
        "payee": seen.payees.get(bill.party_id) if bill.party_id else None,
    }
    digest = _hash({**read, "rules": [asdict(result) for result in results]})
    if decision is not None and decision.picture_hash == digest and not force:
        return False

    mirrored = policies.mirror(amount, bill.currency)
    now = utcnow()
    if decision is None:
        # A bill from nobody the books know is never proposed; one whose party the books
        # cleared since is held, never left waiting as it was.
        if bill.party_id is None:
            return False
        decision = Decision(
            company_id=company_id, invoice_id=bill.id, party_id=bill.party_id, evaluation=0
        )
    decision.party_id = bill.party_id or decision.party_id
    decision.state = _STATES[outcome]
    decision.evaluation += 1
    decision.picture_hash = digest
    decision.policy_version = policy.version
    decision.amount = amount
    decision.currency = bill.currency
    decision.rate = mirrored.rate if mirrored is not None else None
    decision.usdc_units = mirrored.units if mirrored is not None else None
    decision.rules = [asdict(result) | {"outcome": result.outcome.value} for result in results]
    decision.decided_by, decision.decided_at = None, None
    decision.evaluated_at = now
    await repository.add(db, decision)
    await _log(
        db,
        decision,
        LogEvent.PROPOSED,
        {
            "evaluation": decision.evaluation,
            "state": decision.state.value,
            "invoice_id": str(bill.id),
            "amount": str(amount),
            "currency": bill.currency,
            "rate": _text(decision.rate),
            "usdc_units": decision.usdc_units,
            "policy_version": policy.version,
            "picture_hash": digest,
            **read,
            "rules": decision.rules,
        },
        user_id=None,
        at=now,
    )
    return True


async def _picture(
    db: AsyncSession,
    company_id: uuid.UUID,
    bill: InvoiceOut,
    amount: Decimal,
    policy: PolicyOut,
    seen: _OnChain,
) -> Picture:
    """What code knows of the bill, for the rules."""
    links = await projects.links_of_items(db, company_id, ItemKind.INVOICE, [bill.id])
    jobs = sorted({link.project_id for link in links if projects.is_evidence(link)})
    client_bills = [
        link.item_id
        for link in await projects.links_of_projects(db, company_id, jobs)
        if link.item_kind is ItemKind.INVOICE and projects.is_evidence(link)
    ]
    clients = [
        invoice
        for invoice in await books.get_invoices(db, company_id, client_bills)
        if invoice.direction is InvoiceDirection.RECEIVABLE and not invoice.cancelled
    ]
    trucks = tuple(await _delivery[0](db, company_id, bill.id)) if _delivery and jobs else None
    mirrored = policies.mirror(amount, bill.currency)
    payee = seen.payees.get(bill.party_id) if bill.party_id else None
    return Picture(
        amount=amount,
        left=bill.remaining_amount,
        cancelled=bill.cancelled,
        party=bill.party_id is not None,
        currency=bill.currency,
        usdc_units=mirrored.units if mirrored is not None else None,
        in_policy_currency=policies.convert(amount, bill.currency, policy.currency),
        projects=len(jobs),
        trucks=trucks,
        client_invoices=len(clients),
        client_unpaid=sum(
            1 for c in clients if c.remaining_amount is None or c.remaining_amount > 0
        ),
        paid_on_chain=bill.id in seen.paid,
        paid_as_another=await repository.approved_among(
            db, company_id, await books.namesakes(db, company_id, bill.id)
        ),
        vault=seen.vault,
        payee=payee is not None,
        payee_active_from=seen.active_from.get(payee) if payee is not None else None,
        now=int(utcnow().timestamp()),
    )


# The owner's answer


async def act(
    db: AsyncSession, actor: Actor, decision_id: uuid.UUID, body: DecisionAction
) -> DecisionOut:
    """The owner answers the evaluation in force. Approving needs every rule to allow it; a hold
    or a rejection by the owner stands until the owner answers again; a new amount is a new
    proposal, every rule run again, back to the owner. Commits."""
    actor.require(Role.OWNER)
    decision = await repository.get(db, actor.company_id, decision_id)
    if decision is None:
        raise NotFound("No such decision")
    bills = await books.get_invoices(db, actor.company_id, [decision.invoice_id])
    if not bills:
        raise NotFound("No such bill")
    bill = bills[0]
    # Read before the lock is taken: the chain is not asked with the company's decisions held.
    rests = body.action in ("edit", "approve")
    seen = await _on_chain(actor.company_id, [bill]) if rests else _OnChain()

    await repository.lock_decisions(db, actor.company_id)
    # As it is now, an evaluation or another answer may have come in meanwhile.
    await db.refresh(decision)
    if body.evaluation != decision.evaluation:
        raise Conflict("It was evaluated again since: answer the evaluation in force")
    if decision.state in _EXECUTORS:
        raise Conflict("It is approved already")
    if (
        body.action == "approve"
        and decision.state is DecisionState.FAILED
        and decision.decided_by is not None
    ):
        raise Conflict("The vault refused it for good: it waits for someone to look at it")
    now = utcnow()
    if body.action == "edit" and body.amount is not None:
        await _log(
            db,
            decision,
            LogEvent.EDITED,
            {
                "evaluation": decision.evaluation,
                "from": str(decision.amount),
                "to": str(body.amount),
            },
            user_id=actor.user_id,
            at=now,
        )
        decision.amount, decision.amount_edited = body.amount, True
        await _decide(db, actor.company_id, bill, decision, seen, force=True)
    else:
        if body.action == "approve":
            # What it rests on is read again under the lock: the books, the links, the proof,
            # the chain, and whether another booking of the bill was approved since (so two
            # approvals in a row never pay it twice), also for a decision the owner held, which
            # no pass evaluates. Changed, it is a new evaluation, which the owner answers anew.
            if await _decide(db, actor.company_id, bill, decision, seen):
                await db.commit()
                raise Conflict("It was evaluated again since: answer the evaluation in force")
            if not _approvable(decision):
                raise Conflict("A rule holds or rejects it: it cannot be approved")
        decision.state = {
            "approve": DecisionState.APPROVED,
            "hold": DecisionState.HELD,
            "reject": DecisionState.REJECTED,
        }[body.action]
        decision.decided_by, decision.decided_at = actor.user_id, now
        await _log(
            db,
            decision,
            LogEvent(decision.state.value),
            {
                "evaluation": decision.evaluation,
                "amount": str(decision.amount),
                "usdc_units": decision.usdc_units,
            },
            user_id=actor.user_id,
            at=now,
        )
    await db.commit()
    return DecisionOut.model_validate(decision)


def _approvable(decision: Decision) -> bool:
    """Every rule passed or left it to the owner, and its test USDC is known."""
    held = {Outcome.HOLD.value, Outcome.REJECT.value}
    return decision.usdc_units is not None and not any(
        result["outcome"] in held for result in decision.rules
    )


# For the chat (assistant): what waits for the owner


async def awaiting_owner(db: AsyncSession, company_id: uuid.UUID) -> list[DecisionOut]:
    """The decisions that wait for the owner's approval, the earliest evaluated first, each with
    its bill."""
    rows = await repository.awaiting_owner(db, company_id)
    bills = {
        bill.id: bill
        for bill in await books.get_invoices(db, company_id, [row.invoice_id for row in rows])
    }
    return [
        DecisionOut.model_validate(row).model_copy(update={"invoice": bills.get(row.invoice_id)})
        for row in rows
    ]


async def companies_awaiting_owner(db: AsyncSession) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone."""
    return await repository.companies_awaiting_owner(db)


async def waits_for_owner(db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID) -> bool:
    """Read under the company's lock, held until the caller commits: an owner's answer then
    comes before what the caller keeps about it, or after, never between."""
    await repository.lock_decisions(db, company_id)
    decision = await repository.get(db, company_id, decision_id)
    return decision is not None and decision.state is DecisionState.AWAITING_OWNER


async def jobs_of(
    db: AsyncSession, company_id: uuid.UUID, invoice_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """The projects that hold each bill as evidence: where a word about it has its context."""
    links = await projects.links_of_items(db, company_id, ItemKind.INVOICE, invoice_ids)
    jobs: dict[uuid.UUID, set[uuid.UUID]] = {}
    for link in links:
        if projects.is_evidence(link):
            jobs.setdefault(link.item_id, set()).add(link.project_id)
    return {invoice_id: sorted(held) for invoice_id, held in jobs.items()}


# The executor's results (settlement)


async def approved(db: AsyncSession, company_id: uuid.UUID) -> list[DecisionOut]:
    """The decisions the owner approved and the executor has not paid, the earliest approved
    first."""
    return [DecisionOut.model_validate(row) for row in await repository.approved(db, company_id)]


async def companies_with_approved(db: AsyncSession) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone."""
    return await repository.companies_with_approved(db)


async def record_paid(
    db: AsyncSession,
    company_id: uuid.UUID,
    decision_id: uuid.UUID,
    evaluation: int,
    *,
    tx_hash: str,
    transaction_id: str,
) -> None:
    """The vault paid the approved evaluation: settled, never without the transaction's hash.
    Under the decisions' lock; no commit."""
    decision = await _approved(db, company_id, decision_id, evaluation)
    if decision is None:
        return
    decision.state = DecisionState.SETTLED
    await _log(
        db,
        decision,
        LogEvent.SETTLED,
        {
            "evaluation": evaluation,
            "usdc_units": decision.usdc_units,
            "tx_hash": tx_hash,
            "transaction_id": transaction_id,
        },
        user_id=None,
        at=utcnow(),
    )


async def record_would_release(
    db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID, evaluation: int, payee: str
) -> None:
    """A replay company's approval, recorded and never sent (ADR-0009): it stays approved. No
    commit."""
    decision = await _approved(db, company_id, decision_id, evaluation)
    if decision is None:
        return
    await _log(
        db,
        decision,
        LogEvent.WOULD_RELEASE,
        {"evaluation": evaluation, "usdc_units": decision.usdc_units, "payee": payee},
        user_id=None,
        at=utcnow(),
    )


async def record_refused(
    db: AsyncSession,
    company_id: uuid.UUID,
    decision_id: uuid.UUID,
    evaluation: int,
    reason: str,
    *,
    final: bool,
) -> None:
    """The vault refused the payment. ``final``: it had paid the key already, which another
    evaluation will not change, so the decision fails for someone to look at. Otherwise (a cap,
    the balance, a payee not active yet, changed on the chain since the evaluation) it is
    evaluated again on the next pass and goes back to the owner. No commit."""
    decision = await _approved(db, company_id, decision_id, evaluation)
    if decision is None:
        return
    decision.state = DecisionState.FAILED
    if not final:
        decision.decided_by, decision.decided_at = None, None
        # Whatever it was made from, it is evaluated again.
        decision.picture_hash = ""
    await _log(
        db,
        decision,
        LogEvent.FAILED,
        {"evaluation": evaluation, "reason": reason[:300], "evaluated_again": not final},
        user_id=None,
        at=utcnow(),
    )


async def _approved(
    db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID, evaluation: int
) -> Decision | None:
    """The decision, under the company's lock, if it is still approved for that evaluation."""
    await repository.lock_decisions(db, company_id)
    decision = await repository.get(db, company_id, decision_id)
    if decision is not None:
        await db.refresh(decision)
    if (
        decision is None
        or decision.state is not DecisionState.APPROVED
        or decision.evaluation != evaluation
    ):
        return None
    return decision


# Reading


async def list_decisions(
    db: AsyncSession,
    actor: Actor,
    *,
    states: Sequence[DecisionState] | None,
    invoice_id: uuid.UUID | None,
    party_id: uuid.UUID | None,
    with_invoices: bool = False,
    page: PageParams,
) -> DecisionPage:
    rows, total = await repository.list_decisions(
        db,
        actor.company_id,
        states=states,
        invoice_id=invoice_id,
        party_id=party_id,
        page=page,
    )
    items = [DecisionOut.model_validate(row) for row in rows]
    if with_invoices:
        bills = {
            bill.id: bill
            for bill in await books.get_invoices(
                db, actor.company_id, [item.invoice_id for item in items]
            )
        }
        items = [item.model_copy(update={"invoice": bills.get(item.invoice_id)}) for item in items]
    return DecisionPage(items=items, total=total)


async def get_decision(
    db: AsyncSession, actor: Actor, decision_id: uuid.UUID, *, with_invoice: bool = False
) -> DecisionOut:
    decision = await repository.get(db, actor.company_id, decision_id)
    if decision is None:
        raise NotFound("No such decision")
    out = DecisionOut.model_validate(decision)
    if with_invoice:
        bills = await books.get_invoices(db, actor.company_id, [decision.invoice_id])
        out = out.model_copy(update={"invoice": bills[0] if bills else None})
    return out


async def list_events(
    db: AsyncSession,
    actor: Actor,
    *,
    decision_id: uuid.UUID | None,
    newest_first: bool = False,
    with_invoices: bool = False,
    page: PageParams,
) -> DecisionEventPage:
    rows, total = await repository.list_events(
        db, actor.company_id, decision_id=decision_id, newest_first=newest_first, page=page
    )
    items = [
        DecisionEventOut.model_validate(row).model_copy(
            update={
                "explorer_url": settlement.explorer_url(tx)
                if (tx := row.body.get("tx_hash"))
                else None
            }
        )
        for row in rows
    ]
    if with_invoices:
        bill_of = await repository.invoices_of(
            db, actor.company_id, list({item.decision_id for item in items})
        )
        bills = {
            bill.id: bill
            for bill in await books.get_invoices(db, actor.company_id, list(set(bill_of.values())))
        }
        # The firm of each event's evaluation, as it was logged then.
        party_of = {
            (row.decision_id, row.body.get("evaluation")): row.body.get("party")
            for row in await repository.proposals_of(db, actor.company_id, list(bill_of))
        }
        at_the_time = {
            item.id: party_of.get((item.decision_id, item.body.get("evaluation"))) for item in items
        }
        names = await parties.get_parties(
            db,
            actor.company_id,
            list({uuid.UUID(party) for party in at_the_time.values() if party}),
        )
        items = [
            item.model_copy(
                update={
                    "invoice": bills.get(bill_of[item.decision_id]),
                    "party_name": (
                        names[uuid.UUID(party)].name
                        if (party := at_the_time[item.id]) and uuid.UUID(party) in names
                        else None
                    ),
                }
            )
            for item in items
        ]
    return DecisionEventPage(items=items, total=total)


async def chain_state(db: AsyncSession, actor: Actor) -> DecisionChainOut:
    rows = await repository.chain_of(db, actor.company_id)
    return DecisionChainOut(
        length=len(rows), head=rows[-1].hash if rows else None, broken_at=_broken(rows)
    )


# The log


async def _log(
    db: AsyncSession,
    decision: Decision,
    event: LogEvent,
    body: dict[str, Any],
    *,
    user_id: uuid.UUID | None,
    at: datetime,
) -> None:
    """Appends one event to the company's chain, under the caller's ``lock_decisions``."""
    last = await repository.last_event(db, decision.company_id)
    seq = 1 if last is None else last.seq + 1
    previous = GENESIS if last is None else last.hash
    row = DecisionEvent(
        company_id=decision.company_id,
        decision_id=decision.id,
        seq=seq,
        event=event,
        body=body,
        user_id=user_id,
        at=at,
        previous_hash=previous,
    )
    row.hash = _hash(_content(row))
    await repository.add(db, row)
    # Every change to a decision is a row here: the pages showing decisions read them again.
    await events.publish(db, decision.company_id, events.EventKind.DECISIONS)


def _content(row: DecisionEvent) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "decision_id": str(row.decision_id),
        "event": LogEvent(row.event).value,
        "body": row.body,
        "user_id": str(row.user_id) if row.user_id is not None else None,
        "at": row.at.isoformat(),
        "previous_hash": row.previous_hash,
    }


async def broken_link(db: AsyncSession, company_id: uuid.UUID) -> int | None:
    """The first row of the company's chain whose hash does not hold, or None when every one
    does: a row changed, removed or put in breaks the chain from there."""
    return _broken(await repository.chain_of(db, company_id))


def _broken(rows: Sequence[DecisionEvent]) -> int | None:
    previous = GENESIS
    for expected, row in enumerate(rows, start=1):
        if row.seq != expected or row.previous_hash != previous or row.hash != _hash(_content(row)):
            return expected
        previous = row.hash
    return None


def _hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _jsonable(value: Any) -> Any:
    """As stored in JSON and read back the same: amounts as text, never floats."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
