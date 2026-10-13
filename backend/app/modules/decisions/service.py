"""Decisions (ADR-0002, [05](../../../../docs/architecture/05-decisions-and-settlement.md)): what
Tamga decides about each bill the company has to pay, and its history.

Each payable is evaluated again whenever what code knows of it changed: its picture is built from
the books, the confirmed links, what a sector pack says of its delivery and the vault on the
chain; the hard rules (``policy``) give the outcome; and an evaluation that differs from the last
one is a new proposal, logged. The owner answers the evaluation in force (approve, hold, reject,
or a new amount or way to pay, which is evaluated again). No model takes part, and nothing here
moves money: the executor pays an approved decision from the vault (``settlement``), and one paid
by bank transfer is the owner's to pay, settled by the bank movement matched to it or by the
owner marking it paid (ADR-0027).

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
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.books import accounts
from app.modules.books import service as books
from app.modules.books.accounts import Account
from app.modules.books.models import InvoiceDirection
from app.modules.books.schemas import InvoiceOut, Settled
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.decisions import anchoring, repository
from app.modules.decisions.models import (
    AdviceRequest,
    Decision,
    DecisionAnchor,
    DecisionEvent,
    DecisionState,
    LogEvent,
)
from app.modules.decisions.schemas import (
    ChainAnchorOut,
    DecisionAction,
    DecisionChainOut,
    DecisionEventOut,
    DecisionEventPage,
    DecisionOut,
    DecisionPage,
    DecisionSort,
)
from app.modules.fx import service as fx
from app.modules.money import service as money
from app.modules.money.schemas import MovementOut
from app.modules.parties import service as parties
from app.modules.parties.models import IdentifierKind
from app.modules.policy import service as policies
from app.modules.policy.models import PaymentMethod, PaymentsRecorded
from app.modules.policy.schemas import Outcome, Picture, PolicyOut, Vault
from app.modules.projects import service as projects
from app.modules.settlement import service as settlement
from app.modules.settlement.schemas import PaymentOut
from app.platform import chain, clock, events, jobs
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.items import ItemKind
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

# The first row of a company's chain follows this.
GENESIS: Final = "0" * 64
# What ADVICE_PER_DAY counts the controller's requests over.
ADVICE_WINDOW: Final = timedelta(hours=24)
_CENT: Final = Decimal("0.01")

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


@dataclass(slots=True)
class _Standing:
    """The company's money this pass, read under one policy: what the reserve rule and the pay
    currency read. Amounts are in the policy's currency, None where a rate is missing."""

    policy: PolicyOut
    today: date
    # The bank and cash accounts' total, None where unknown; the currencies they are held in.
    balance: Decimal | None = None
    currencies: frozenset[str] = frozenset()
    # Each open payable's due day and what is left of it.
    dues: dict[uuid.UUID, tuple[date | None, Decimal | None]] = field(default_factory=dict)
    # The bills approved and not paid yet.
    approved: dict[uuid.UUID, Decimal | None] = field(default_factory=dict)

    def committed(self, bill_id: uuid.UUID) -> Decimal | None:
        """What the reserve keeps for bills other than this one: the approved ones, and those due
        within its weeks or overdue."""
        horizon = self.today + timedelta(weeks=self.policy.reserve.cover_weeks)
        kept = [amount for invoice_id, amount in self.approved.items() if invoice_id != bill_id]
        kept += [
            amount
            for invoice_id, (due, amount) in self.dues.items()
            if invoice_id != bill_id
            and invoice_id not in self.approved
            and due is not None
            and due <= horizon
        ]
        if any(amount is None for amount in kept):
            return None
        return sum((amount for amount in kept if amount is not None), Decimal(0))


async def _standing(db: AsyncSession, company_id: uuid.UUID, policy: PolicyOut) -> _Standing:
    company = (await companies.get_companies(db, [company_id]))[company_id]
    today = clock.now(company).date()
    position = await money.position(db, company_id, policy.currency)
    rates: dict[str | None, Decimal | None] = {}

    async def in_policy(amount: Decimal, currency: str | None) -> Decimal | None:
        if currency not in rates:
            one = await fx.convert(
                db, Decimal(1), currency, policy.currency, on=today, country=company.country
            )
            rates[currency] = one.rate if one is not None else None
        rate = rates[currency]
        return None if rate is None else (amount * rate).quantize(_CENT)

    dues = {
        bill.id: (bill.due_date, await in_policy(bill.remaining_amount, bill.currency))
        for bill in await books.open_payables(db, company_id)
        if not bill.cancelled and bill.remaining_amount is not None and bill.remaining_amount > 0
    }
    approved = {
        row.invoice_id: await in_policy(row.amount, row.currency)
        for row in await repository.approved(db, company_id)
    }
    return _Standing(
        policy=policy,
        today=today,
        balance=position.total,
        currencies=position.currencies,
        dues=dues,
        approved=approved,
    )


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


async def evaluate_later(db: AsyncSession, company_id: uuid.UUID) -> None:
    """Has one company's bills weighed within the minute, in the caller's transaction: a
    sandbox that just opened, or whose day just moved, does not wait for the next pass."""
    from app.modules.decisions import tasks

    await jobs.defer(
        db,
        tasks.evaluate_one,
        args={"company_id": str(company_id)},
        lock=f"decisions:{company_id}",
    )


async def evaluate_company(company_id: uuid.UUID) -> int:
    """One company's pass, each bill in a transaction of its own. A replay company's decisions
    are made at its own clock's time, not here (ADR-0009)."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        if company is None or company.mode is CompanyMode.REPLAY:
            return 0
        bills = {bill.id: bill for bill in await books.open_payables(db, company_id)}
        # A bill no longer open whose decision still waits is evaluated too: paid or cancelled
        # in the books meanwhile, it is not left waiting for an approval.
        missing = [i for i in await repository.movable_invoices(db, company_id) if i not in bills]
        for bill in await books.get_invoices(db, company_id, missing):
            bills[bill.id] = bill
    if not bills:
        return 0
    seen = await _on_chain(company_id, list(bills.values()))
    async with open_session() as db:
        standing = await _standing(db, company_id, await policies.current_policy(db, company_id))
    moved = 0
    for bill in bills.values():
        async with open_session() as db:
            await repository.lock_decisions(db, company_id)
            decision = await repository.for_invoice(db, company_id, bill.id)
            if decision is not None:
                # The list sorts by the bill's date, which the books may correct in any state.
                decision.issued_on = bill.issue_date
            if (decision is None or _movable(decision)) and await _decide(
                db, company_id, bill, decision, seen, standing
            ):
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
    standing: _Standing,
    *,
    force: bool = False,
) -> bool:
    """Evaluates one bill and keeps the result when it differs from the last evaluation (or
    ``force``, after the owner's edit): a new evaluation number, the state the rules give, and a
    ``proposed`` row in the log. Returns whether it did. Under the caller's lock; no commit."""
    policy = await policies.current_policy(db, company_id)
    if policy.version != standing.policy.version:
        # Changed since the pass began: its amounts may be in another currency.
        standing = await _standing(db, company_id, policy)
    readings = await books.account_readings(
        db, company_id, [bill.party_id] if bill.party_id else []
    )
    account = readings.get(bill.id)
    amount = (
        decision.amount
        if decision is not None and decision.amount_edited
        else _to_pay(bill, account)
    )
    company = (await companies.get_companies(db, [company_id]))[company_id]
    # At the company's own time: a sandbox skipped ahead compares at that day's rate.
    reference = await fx.convert(
        db,
        amount,
        bill.currency,
        policy.currency,
        on=clock.now(company).date(),
        country=company.country,
    )
    payee = seen.payees.get(bill.party_id) if bill.party_id else None
    ibans = (
        await parties.identifiers_of(db, company_id, [bill.party_id], [IdentifierKind.IBAN])
        if bill.party_id
        else {}
    )
    iban = bool(ibans.get(bill.party_id)) if bill.party_id else False
    method = (
        decision.method
        if decision is not None and decision.pay_chosen
        else _propose_method(policy, vault=payee is not None, bank=iban)
    )
    picture = await _picture(
        db, company_id, bill, amount, account, policy, seen, reference, method, iban, standing
    )
    outcome, results = policies.evaluate(picture, policy)
    mirrored = policies.mirror(amount, bill.currency)
    pay = await _pay(
        db,
        bill,
        amount,
        method,
        mirrored,
        chosen=decision.pay_currency if decision is not None and decision.pay_chosen else None,
        standing=standing,
        country=company.country,
    )
    paid = outcome is Outcome.HOLD and _paid_in_books(bill, account, policy)
    # What the rules read, but the time and the converted amount, and what they gave: a timelock
    # that runs out, or a day's rate that moves a bill across a limit, changes a result, so the
    # decision moves then; a day's rate alone does not (ADR-0028). Whom it pays is part of it: a
    # bill the books move to another party is a new proposal, never an approval of the old one.
    read = {
        "picture": _jsonable(asdict(picture) | {"now": None}),
        "policy": policy.version,
        "party": str(bill.party_id),
        "payee": payee,
        "method": method.value,
        "pay_currency": pay.currency,
        # Present only when true: a bill whose books show it paid is evaluated anew, and no other
        # bill's evaluation changes with it.
        **({"paid_in_books": True} if paid else {}),
    }
    digest = _hash(
        {
            **read,
            # The figures that move with a day's rate or a balance: what they change is in the
            # rules' results.
            "picture": read["picture"]
            | {"in_policy_currency": None, "balance": None, "committed": None},
            "rules": [asdict(result) for result in results],
        }
    )
    if decision is not None and decision.picture_hash == digest and not force:
        return False

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
    decision.state = DecisionState.PAID_IN_BOOKS if paid else _STATES[outcome]
    decision.evaluation += 1
    decision.picture_hash = digest
    decision.policy_version = policy.version
    decision.amount = amount
    decision.currency = bill.currency
    decision.rate = mirrored.rate if mirrored is not None else None
    decision.usdc_units = mirrored.units if mirrored is not None else None
    converted = reference if reference is not None and reference.source is not None else None
    decision.reference_rate = converted.rate if converted is not None else None
    decision.reference_rate_on = converted.on if converted is not None else None
    decision.reference_source = converted.source if converted is not None else None
    decision.method = method
    decision.pay_currency, decision.pay_amount = pay.currency, pay.amount
    decision.pay_rate, decision.pay_rate_on, decision.pay_rate_source = pay.rate, pay.on, pay.source
    decision.rules = [asdict(result) | {"outcome": result.outcome.value} for result in results]
    decision.issued_on = bill.issue_date
    decision.decided_by, decision.decided_at = None, None
    # An opinion on the evaluation before is no opinion on this one.
    decision.advice = None
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
            "reference_rate": _text(decision.reference_rate),
            "reference_rate_on": (
                decision.reference_rate_on.isoformat() if decision.reference_rate_on else None
            ),
            "reference_source": decision.reference_source,
            "pay_amount": _text(pay.amount),
            "pay_rate": _text(pay.rate),
            "pay_rate_on": pay.on.isoformat() if pay.on else None,
            "pay_rate_source": pay.source,
            "policy_version": policy.version,
            "picture_hash": digest,
            **read,
            "rules": decision.rules,
        },
        user_id=None,
        at=now,
    )
    return True


def _paid_in_books(bill: InvoiceOut, account: Account | None, policy: PolicyOut) -> bool:
    """Whether the books show the bill paid, as the company's setting reads them (ADR-0022): with
    nothing left on the invoice, or, recorded on account, with nothing the firm's account lays on
    it. Read per invoice, an account that shows it paid is a doubt the owner looks at, and the
    bill stays held."""
    left = bill.remaining_amount
    if bill.cancelled or left is None:
        return False
    if left <= 0:
        return True
    if policy.payments_recorded is not PaymentsRecorded.ON_ACCOUNT or account is None:
        return False
    return account.owed is not None and account.owed <= 0


def _to_pay(bill: InvoiceOut, account: Account | None) -> Decimal:
    """What is left of a bill, or less where the firm's account says less is owed on it (ADR-0022);
    one the account shows paid keeps the books' figure, for the rule to hold."""
    left = bill.remaining_amount or Decimal("0.00")
    owed = account.owed if account is not None else None
    return owed if owed is not None and 0 < owed < left else left


async def _picture(
    db: AsyncSession,
    company_id: uuid.UUID,
    bill: InvoiceOut,
    amount: Decimal,
    account: Account | None,
    policy: PolicyOut,
    seen: _OnChain,
    reference: fx.Converted | None,
    method: PaymentMethod,
    iban: bool,
    standing: _Standing,
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
    on_clients = await books.account_readings(
        db, company_id, {client.party_id for client in clients if client.party_id}
    )
    trucks = tuple(await _delivery[0](db, company_id, bill.id)) if _delivery and jobs else None
    mirrored = policies.mirror(amount, bill.currency)
    payee = seen.payees.get(bill.party_id) if bill.party_id else None
    return Picture(
        amount=amount,
        left=bill.remaining_amount,
        owed_on_account=account.owed if account is not None else None,
        account_on=account.on if account is not None else None,
        within_tolerance=accounts.within(bill, policy.remainder_tolerance),
        cancelled=bill.cancelled,
        party=bill.party_id is not None,
        currency=bill.currency,
        usdc_units=mirrored.units if mirrored is not None else None,
        in_policy_currency=reference.amount if reference is not None else None,
        projects=len(jobs),
        trucks=trucks,
        client_invoices=len(clients),
        client_unpaid=sum(
            1 for client in clients if not _client_paid(client, on_clients.get(client.id), policy)
        ),
        paid_on_chain=bill.id in seen.paid,
        paid_as_another=await repository.approved_among(
            db, company_id, await books.namesakes(db, company_id, bill.id)
        ),
        vault=seen.vault,
        payee=payee is not None,
        payee_active_from=seen.active_from.get(payee) if payee is not None else None,
        now=int(utcnow().timestamp()),
        method=method,
        iban=iban,
        balance=standing.balance,
        committed=standing.committed(bill.id),
    )


def _propose_method(policy: PolicyOut, *, vault: bool, bank: bool) -> PaymentMethod:
    """The policy's default, unless the payee can take only another of its methods: no wallet on
    the vault, or no IBAN (ADR-0027). Code chooses it; no model does."""
    can = {PaymentMethod.VAULT_USDC: vault, PaymentMethod.BANK_TRANSFER: bank}
    if can[policy.default_method]:
        return policy.default_method
    return next((method for method in policy.methods if can[method]), policy.default_method)


@dataclass(frozen=True, slots=True)
class _Pay:
    """What leaves the company's account, expected: the currency, the amount at the reference
    rate of the company's day, one unit of the bill's currency in it, that day and the source."""

    currency: str | None
    amount: Decimal | None
    rate: Decimal | None
    on: date | None
    source: str | None


async def _pay(
    db: AsyncSession,
    bill: InvoiceOut,
    amount: Decimal,
    method: PaymentMethod,
    mirrored: policies.Mirror | None,
    *,
    chosen: str | None,
    standing: _Standing,
    country: str,
) -> _Pay:
    """From the vault, test USDC at the mirror rate (ADR-0019). By bank: the currency the owner
    chose, else the bill's own when the company holds an account in it (or none is known), else
    the policy's when held, else one it holds."""
    if method is PaymentMethod.VAULT_USDC:
        if mirrored is None:
            return _Pay("USDC", None, None, None, None)
        return _Pay("USDC", _six(chain.usdc(mirrored.units)), mirrored.rate, None, "mirror")
    held = standing.currencies
    if chosen is not None and len(chosen) == 3:
        currency: str | None = chosen
    elif not held or bill.currency in held:
        currency = bill.currency
    elif standing.policy.currency in held:
        currency = standing.policy.currency
    else:
        currency = sorted(held)[0]
    if currency is None:
        return _Pay(None, None, None, None, None)
    converted = await fx.convert(
        db, amount, bill.currency, currency, on=standing.today, country=country
    )
    if converted is None:
        return _Pay(currency, None, None, None, None)
    return _Pay(currency, _six(converted.amount), converted.rate, converted.on, converted.source)


def _six(amount: Decimal) -> Decimal:
    """As ``pay_amount`` keeps it, so an answer reads the same as the row read back."""
    return amount.quantize(Decimal("0.000001"))


def _client_paid(invoice: InvoiceOut, account: Account | None, policy: PolicyOut) -> bool:
    """Whether a client invoice counts as paid for pay-when-paid: the books show it paid, or what
    is left is within the tolerance, or the company records payments on account and the client's
    account shows it paid."""
    if invoice.remaining_amount is None:
        return False
    read = accounts.settled(invoice, account, policy.payments_recorded, policy.remainder_tolerance)
    return read in (None, Settled.PAID_ON_ACCOUNT, Settled.PAID_WITH_DIFFERENCE)


# The owner's answer


async def act(
    db: AsyncSession, actor: Actor, decision_id: uuid.UUID, body: DecisionAction
) -> DecisionOut:
    """The owner answers the evaluation in force. Approving needs every rule to allow it; a hold
    or a rejection by the owner stands until the owner answers again; a new amount or way to pay
    is a new proposal, every rule run again, back to the owner; marking it paid settles a bill the
    owner pays in the bank, never one Tamga pays from the vault. Commits."""
    actor.require(Role.OWNER)
    decision = await repository.get(db, actor.company_id, decision_id)
    if decision is None:
        raise NotFound("No such decision")
    bills = await books.get_invoices(db, actor.company_id, [decision.invoice_id])
    if not bills:
        raise NotFound("No such bill")
    bill = bills[0]
    # Read before the lock is taken: the chain is not asked with the company's decisions held.
    rests = body.action in ("edit", "approve", "pay_by", "mark_paid")
    seen = await _on_chain(actor.company_id, [bill]) if rests else _OnChain()

    await repository.lock_decisions(db, actor.company_id)
    # As it is now, an evaluation or another answer may have come in meanwhile.
    await db.refresh(decision)
    if body.evaluation != decision.evaluation:
        raise Conflict("It was evaluated again since: answer the evaluation in force")
    if body.action == "undo_paid":
        await _undo_paid(db, actor, decision, body.reason, utcnow())
        await db.commit()
        return DecisionOut.model_validate(decision)
    if decision.state is DecisionState.SETTLED:
        raise Conflict("It is paid already")
    if decision.state is DecisionState.APPROVED and body.action != "mark_paid":
        raise Conflict("It is approved already")
    if (
        body.action == "approve"
        and decision.state is DecisionState.FAILED
        and decision.decided_by is not None
    ):
        raise Conflict("The vault refused it for good: it waits for someone to look at it")
    standing = (
        await _standing(db, actor.company_id, await policies.current_policy(db, actor.company_id))
        if rests
        else None
    )
    now = utcnow()
    if body.action == "edit" and body.amount is not None and standing is not None:
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
        await _decide(db, actor.company_id, bill, decision, seen, standing, force=True)
    elif body.action == "pay_by" and body.method is not None and standing is not None:
        to_currency = body.pay_currency if body.method is PaymentMethod.BANK_TRANSFER else "USDC"
        await _log(
            db,
            decision,
            LogEvent.PAY_BY,
            {
                "evaluation": decision.evaluation,
                "from": {"method": decision.method.value, "pay_currency": decision.pay_currency},
                "to": {"method": body.method.value, "pay_currency": to_currency},
            },
            user_id=actor.user_id,
            at=now,
        )
        decision.method, decision.pay_chosen = body.method, True
        # None: the bank's default currency, worked out by the evaluation.
        decision.pay_currency = to_currency
        await _decide(db, actor.company_id, bill, decision, seen, standing, force=True)
    elif body.action == "mark_paid" and standing is not None:
        await _mark_paid(db, actor, decision, bill, body, seen, standing, now)
    else:
        if body.action == "approve" and standing is not None:
            await _rests_unchanged(db, actor.company_id, bill, decision, seen, standing)
        decision.state = {
            "approve": DecisionState.APPROVED,
            "hold": DecisionState.HELD,
            "reject": DecisionState.REJECTED,
        }[body.action]
        decision.decided_by, decision.decided_at = actor.user_id, now
        await _log_answer(db, decision, actor, now)
    await db.commit()
    return DecisionOut.model_validate(decision)


async def _rests_unchanged(
    db: AsyncSession,
    company_id: uuid.UUID,
    bill: InvoiceOut,
    decision: Decision,
    seen: _OnChain,
    standing: _Standing,
) -> None:
    """What an approval rests on is read again under the lock: the books, the links, the proof,
    the chain, the accounts, and whether another booking of the bill was approved since (so two
    approvals in a row never pay it twice), also for a decision the owner held, which no pass
    evaluates. Changed, it is a new evaluation, which the owner answers anew."""
    if await _decide(db, company_id, bill, decision, seen, standing):
        await db.commit()
        raise Conflict("It was evaluated again since: answer the evaluation in force")
    if not _approvable(decision):
        raise Conflict("A rule holds or rejects it: it cannot be approved")


async def _log_answer(db: AsyncSession, decision: Decision, actor: Actor, at: datetime) -> None:
    await _log(
        db,
        decision,
        LogEvent(decision.state.value),
        {
            "evaluation": decision.evaluation,
            "amount": str(decision.amount),
            "usdc_units": decision.usdc_units,
            "method": decision.method.value,
        },
        user_id=actor.user_id,
        at=at,
    )


async def _mark_paid(
    db: AsyncSession,
    actor: Actor,
    decision: Decision,
    bill: InvoiceOut,
    body: DecisionAction,
    seen: _OnChain,
    standing: _Standing,
    now: datetime,
) -> None:
    """The owner paid it in the bank: approved first if it waited for them, then settled for what
    left their account (ADR-0027)."""
    if policies.tamga_pays(decision.method):
        raise Conflict("Tamga pays it from the vault: it is paid when the vault releases it")
    if body.pay_amount is None or body.pay_currency is None:
        raise BadRequest("What was paid, and its currency")
    paid_on = body.paid_on or standing.today
    if paid_on > standing.today:
        raise BadRequest("A payment is marked on the day it was made, never later")
    if decision.state is not DecisionState.APPROVED:
        if decision.state is not DecisionState.AWAITING_OWNER and decision.decided_by is None:
            raise Conflict("A rule holds or rejects it: it cannot be marked paid")
        await _rests_unchanged(db, actor.company_id, bill, decision, seen, standing)
        decision.state = DecisionState.APPROVED
        decision.decided_by, decision.decided_at = actor.user_id, now
        await _log_answer(db, decision, actor, now)
    payment = await settlement.record_bank_payment(
        db,
        actor.company_id,
        decision,
        pay_amount=body.pay_amount,
        pay_currency=body.pay_currency,
        paid_on=paid_on,
        marked_by=actor.user_id,
    )
    await _settle_by_bank(db, decision, payment, {"marked_by": str(actor.user_id)}, now)


async def _undo_paid(
    db: AsyncSession, actor: Actor, decision: Decision, reason: str | None, now: datetime
) -> None:
    """The owner says a bank transfer settled by mistake was not paid: their own word marked by
    mistake, or a movement matched to the wrong bill. The payment is kept out of force, its
    movement is never matched to this bill again, and the bill is approved and unpaid as before;
    the reason goes on the log. A vault release is on the chain, and has no undo."""
    if decision.state is not DecisionState.SETTLED:
        raise Conflict("It is not marked paid")
    if policies.tamga_pays(decision.method):
        raise Conflict("The vault paid it on the chain: that cannot be undone")
    payment = await settlement.undo_bank_payment(db, actor.company_id, decision, by=actor.user_id)
    if payment is None:
        raise Conflict("It has no bank payment to undo")
    if payment.movement_id is not None:
        await money.unlink_from_bill(db, actor.company_id, payment.movement_id, decision.invoice_id)
    decision.state = DecisionState.APPROVED
    await _log(
        db,
        decision,
        LogEvent.UNPAID,
        {
            "evaluation": decision.evaluation,
            "payment_id": str(payment.id),
            "pay_amount": _text(payment.pay_amount),
            "pay_currency": payment.pay_currency,
            "paid_on": payment.paid_on.isoformat() if payment.paid_on else None,
            "movement_id": str(payment.movement_id) if payment.movement_id else None,
            "reason": " ".join(reason.split()) if reason else None,
        },
        user_id=actor.user_id,
        at=now,
    )


async def _settle_by_bank(
    db: AsyncSession,
    decision: Decision,
    payment: PaymentOut,
    proof: dict[str, str],
    at: datetime,
) -> None:
    """A bank transfer settled: what was paid, the proof, the rate the bank used and how far it
    was from the reference (ADR-0027). What the bill owed is settled in its own currency."""
    paid, currency = payment.pay_amount or Decimal(0), payment.pay_currency
    expected = decision.pay_amount if decision.pay_currency == currency else None
    decision.state = DecisionState.SETTLED
    await _log(
        db,
        decision,
        LogEvent.SETTLED,
        {
            "evaluation": decision.evaluation,
            "method": decision.method.value,
            "pay_amount": str(paid),
            "pay_currency": currency,
            "paid_on": payment.paid_on.isoformat() if payment.paid_on else None,
            **proof,
            "expected_amount": _text(expected),
            "reference_rate": _text(decision.pay_rate),
            # One payment settles the bill, so the bank's rate is what was paid over what it owed.
            "actual_rate": (
                str((paid / decision.amount).quantize(Decimal("0.000001")))
                if currency != decision.currency and decision.amount
                else None
            ),
            "fx_difference": _text(paid - expected) if expected is not None else None,
        },
        user_id=None if "movement_id" in proof else payment.marked_by,
        at=at,
    )


def _approvable(decision: Decision) -> bool:
    """Every rule passed or left it to the owner, and what leaves the account is known: the test
    USDC from the vault, the amount at the reference rate by bank (never a guessed rate)."""
    held = {Outcome.HOLD.value, Outcome.REJECT.value}
    known = (
        decision.usdc_units is not None
        if policies.tamga_pays(decision.method)
        else decision.pay_amount is not None
    )
    return known and not any(result["outcome"] in held for result in decision.rules)


# Bank transfers the owner made, matched to the bills they pay


async def settle_matched() -> int:
    """Settles each approved bill the owner pays by bank whose movement came in: from the firm,
    by its IBAN or tax number, and for its amount within FX_MATCH_TOLERANCE at the movement day's
    reference rate, after the approval (ADR-0027). Two that could be it, or none, leave it to the
    owner. Returns how many were settled."""
    async with open_session() as db:
        company_ids = await repository.companies_with_approved(db)
    settled = 0
    for company_id in company_ids:
        try:
            settled += await _settle_matched(company_id)
        except Exception as error:  # one company never stops the others
            logger.warning("matching the bank of %s failed: %s", company_id, type(error).__name__)
    return settled


async def _settle_matched(company_id: uuid.UUID) -> int:
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        if company is None or company.mode is CompanyMode.REPLAY:
            return 0
        await repository.lock_decisions(db, company_id)
        waiting = [
            row
            for row in await repository.approved(db, company_id)
            if not policies.tamga_pays(row.method) and row.decided_at is not None
        ]
        if not waiting:
            return 0
        since = min(row.decided_at.date() for row in waiting if row.decided_at is not None)
        taken = await settlement.matched_movements(db, company_id)
        undone = await settlement.undone_matches(db, company_id)
        movements = [m for m in await money.outgoing(db, company_id, since) if m.id not in taken]
        known = await parties.identifiers_of(
            db,
            company_id,
            sorted({row.party_id for row in waiting}),
            [IdentifierKind.IBAN, IdentifierKind.TAX_ID],
        )
        rates: dict[tuple[str, str, date], Decimal | None] = {}
        candidates: dict[uuid.UUID, list[MovementOut]] = {}
        for decision in waiting:
            if decision.decided_at is None:
                continue
            approved_on = decision.decided_at.date()
            candidates[decision.id] = [
                movement
                for movement in movements
                if movement.booked_on >= approved_on
                and (decision.id, movement.id) not in undone
                and _from_firm(movement, known.get(decision.party_id, set()), company.country)
                and await _pays(db, movement, decision, company.country, rates)
            ]
        # A movement that could pay two bills pays neither: the owner says which.
        claims: dict[uuid.UUID, int] = {}
        for found in candidates.values():
            for movement in found:
                claims[movement.id] = claims.get(movement.id, 0) + 1
        settled = 0
        for decision in waiting:
            found = candidates.get(decision.id, [])
            if len(found) != 1 or claims[found[0].id] != 1:
                continue
            movement = found[0]
            payment = await settlement.record_bank_payment(
                db,
                company_id,
                decision,
                pay_amount=abs(movement.amount),
                pay_currency=movement.currency,
                paid_on=movement.booked_on,
                movement_id=movement.id,
            )
            await money.link_to_bill(db, company_id, movement.id, decision.invoice_id)
            await _settle_by_bank(
                db, decision, payment, {"movement_id": str(movement.id)}, utcnow()
            )
            settled += 1
        await db.commit()
    return settled


def _from_firm(movement: MovementOut, known: set[tuple[IdentifierKind, str]], country: str) -> bool:
    """The first signal: the movement names the firm's IBAN or tax number."""
    read = [
        (kind, parties.normalised(kind, value, country=country))
        for kind, value in (
            (IdentifierKind.IBAN, movement.counterparty_iban),
            (IdentifierKind.TAX_ID, movement.counterparty_tax_id),
        )
        if value
    ]
    return any(normalised is not None and (kind, normalised) in known for kind, normalised in read)


async def _pays(
    db: AsyncSession,
    movement: MovementOut,
    decision: Decision,
    country: str,
    rates: dict[tuple[str, str, date], Decimal | None],
) -> bool:
    """The second signal: what left the account, in the bill's currency at the movement day's
    reference rate, is within FX_MATCH_TOLERANCE of the bill. Each pair and day's rate is read
    once a pass (``rates``)."""
    into = decision.currency or movement.currency
    key = (movement.currency, into, movement.booked_on)
    if key not in rates:
        one = await fx.convert(
            db, Decimal(1), movement.currency, into, on=movement.booked_on, country=country
        )
        rates[key] = one.rate if one is not None else None
    rate = rates[key]
    if rate is None or not decision.amount:
        return False
    gap = abs(abs(movement.amount) * rate - decision.amount) / decision.amount * 100
    return gap <= get_settings().fx_match_tolerance


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


async def count_awaiting_owner(db: AsyncSession, company_id: uuid.UUID) -> int:
    return await repository.count_awaiting_owner(db, company_id)


async def companies_awaiting_owner(db: AsyncSession) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone."""
    return await repository.companies_awaiting_owner(db)


async def advice_left(db: AsyncSession, company_id: uuid.UUID) -> int:
    """How many more times the controller may be asked for the company today (ADVICE_PER_DAY,
    over the last 24 hours)."""
    spent = await repository.advice_requests_since(db, company_id, utcnow() - ADVICE_WINDOW)
    return max(0, get_settings().advice_per_day - spent)


async def ask_advice(
    db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID, evaluation: int, inputs: str
) -> None:
    """Records that the controller is about to be asked, before the call is sent. Commits."""
    await repository.add(
        db,
        AdviceRequest(
            company_id=company_id,
            decision_id=decision_id,
            evaluation=evaluation,
            inputs=inputs,
            at=utcnow(),
        ),
    )
    await db.commit()


async def keep_advice(
    db: AsyncSession,
    company_id: uuid.UUID,
    decision_id: uuid.UUID,
    evaluation: int,
    advice: dict[str, Any],
) -> bool:
    """Keeps the controller's opinion on an evaluation, in place of any before it, and as a row
    in the log, if that evaluation still waits for the owner: one evaluated again or answered
    meanwhile is not what it weighed. Returns whether it was kept. Commits."""
    await repository.lock_decisions(db, company_id)
    decision = await repository.get(db, company_id, decision_id)
    if (
        decision is None
        or decision.evaluation != evaluation
        or decision.state is not DecisionState.AWAITING_OWNER
    ):
        return False
    decision.advice = advice
    await _log(db, decision, LogEvent.ADVISED, advice, user_id=None, at=utcnow())
    # An open Payments page or chat card shows the opinion without waiting for a reload.
    await events.publish(db, company_id, events.EventKind.DECISIONS)
    await db.commit()
    return True


async def waits_for_owner(db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID) -> bool:
    """Read under the company's lock, held until the caller commits: an owner's answer then
    comes before what the caller keeps about it, or after, never between."""
    await repository.lock_decisions(db, company_id)
    decision = await repository.get(db, company_id, decision_id)
    return decision is not None and decision.state is DecisionState.AWAITING_OWNER


async def still_waiting(
    db: AsyncSession, company_id: uuid.UUID, decision_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these still wait for the owner, read at once under the company's lock, held until
    the caller commits, as ``waits_for_owner`` reads one."""
    await repository.lock_decisions(db, company_id)
    return await repository.awaiting_among(db, company_id, decision_ids)


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
            "method": decision.method.value,
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
    ids: Sequence[uuid.UUID] | None = None,
    with_invoices: bool = False,
    sort: DecisionSort = "-evaluated_at",
    page: PageParams,
) -> DecisionPage:
    rows, total = await repository.list_decisions(
        db,
        actor.company_id,
        states=states,
        invoice_id=invoice_id,
        party_id=party_id,
        ids=ids,
        sort=sort,
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
    anchor = await repository.last_anchor(db, covering=actor.company_id)
    anchored = None
    if anchor is not None:
        head = anchor.heads[str(actor.company_id)]
        seq, hash_ = int(head["seq"]), str(head["hash"])
        # Rows removed from the end leave a chain that holds: only the anchor shows them gone.
        holds = len(rows) >= seq and rows[seq - 1].seq == seq and rows[seq - 1].hash == hash_
        anchored = ChainAnchorOut(
            seq=seq,
            hash=hash_,
            at=anchor.finished_at,
            explorer_url=settlement.explorer_url(anchor.tx_hash) if anchor.tx_hash else None,
            holds=holds,
            on_arc=await _on_arc(anchor),
        )
    return DecisionChainOut(
        length=len(rows),
        head=rows[-1].hash if rows else None,
        broken_at=_broken(rows),
        anchor=anchored,
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


# What the vault recorded in each anchor's transaction: it never changes once read.
_ROOTS_ON_ARC: dict[str, str] = {}


async def _on_arc(anchor: DecisionAnchor) -> bool | None:
    """Whether the anchor's root, computed again from the heads it keeps, is the root its
    transaction recorded on the vault: a row of this database rewritten along with the log
    would not be. None when the chain cannot be read now."""
    if anchor.tx_hash is None or anchoring.root_of(anchor.heads) != anchor.root:
        return False
    if anchor.tx_hash not in _ROOTS_ON_ARC:
        try:
            recorded = await chain.anchored_root(anchor.tx_hash)
        except chain.ChainError:
            return None
        if recorded is None:
            return False
        _ROOTS_ON_ARC[anchor.tx_hash] = recorded
    return _ROOTS_ON_ARC[anchor.tx_hash] == anchor.root


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
    """As stored in JSON and read back the same: amounts as text, never floats; days in ISO."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
