"""A company's money as accounts and movements (ADR-0026), from sources that plug in: the owner's
own (`typed`), an estimate from the books, the vault, a sandbox's scenario, and later a bank
aggregator a country pack registers. No source writes anywhere; each keeps its rows by its own
ids, and the owner's category survives every sync.

Totals across currencies go through the company's reference rates (ADR-0028). Test USDC is a
mirror of a payment, never money held, so it is listed and never counted.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.schemas import InvoiceOut
from app.modules.companies import service as companies
from app.modules.fx import service as fx
from app.modules.money import repository
from app.modules.money.models import (
    AccountKind,
    CategorySetBy,
    MoneyAccount,
    MoneyMovement,
    MovementCategory,
)
from app.modules.money.schemas import (
    AccountChange,
    AccountIn,
    AccountOut,
    AccountPage,
    AccountRecord,
    Holding,
    Holdings,
    MovementChange,
    MovementIn,
    MovementOut,
    MovementPage,
    MovementRecord,
    MovementSort,
)
from app.modules.settlement import service as settlement
from app.platform import chain, clock, events
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

TYPED: Final = "typed"
BOOKS: Final = "books"
VAULT: Final = "vault"
_CENT: Final = Decimal("0.01")


# Reading


async def list_accounts(
    db: AsyncSession, actor: Actor, *, kinds: Sequence[AccountKind] | None
) -> AccountPage:
    rows = await repository.list_accounts(db, actor.company_id, kinds=kinds)
    return AccountPage(items=[AccountOut.model_validate(row) for row in rows], total=len(rows))


async def list_movements(
    db: AsyncSession,
    actor: Actor,
    *,
    account_id: uuid.UUID | None,
    categories: Sequence[MovementCategory] | None,
    uncategorised: bool,
    since: date | None,
    until: date | None,
    estimated: bool | None,
    sort: MovementSort,
    page: PageParams,
) -> MovementPage:
    rows, total = await repository.list_movements(
        db,
        actor.company_id,
        account_id=account_id,
        categories=categories,
        uncategorised=uncategorised,
        since=since,
        until=until,
        estimated=estimated,
        sort=sort,
        page=page,
    )
    return MovementPage(items=[MovementOut.model_validate(row) for row in rows], total=total)


async def holdings(db: AsyncSession, actor: Actor, currency: str) -> Holdings:
    """Each bank and cash account's balance, and their total in ``currency`` at the reference rate
    of the company's own day. One with no rate is listed and left out of the total."""
    return await holdings_of(db, actor.company_id, currency)


@dataclass(frozen=True, slots=True)
class Position:
    """What the payment path reads of the company's money: the bank and cash accounts' total in
    one currency, None when none has a balance or one has no rate; and the currencies the
    company holds an account in."""

    total: Decimal | None
    currencies: frozenset[str]


async def position(db: AsyncSession, company_id: uuid.UUID, currency: str) -> Position:
    held = await holdings_of(db, company_id, currency)
    accounts = await repository.list_accounts(
        db, company_id, kinds=[AccountKind.BANK, AccountKind.CASH]
    )
    return Position(
        total=held.total if held.items and held.complete else None,
        currencies=frozenset(account.currency for account in accounts),
    )


async def outgoing(db: AsyncSession, company_id: uuid.UUID, since: date) -> list[MovementOut]:
    """Money out of a bank or cash account since the day, linked to no bill yet."""
    return [
        MovementOut.model_validate(row) for row in await repository.outgoing(db, company_id, since)
    ]


async def link_to_bill(
    db: AsyncSession, company_id: uuid.UUID, movement_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    """A movement proven to pay a bill: it replaces the bill's estimate from the books on the
    next sync, and is a subcontractor's payment unless the owner said otherwise. No commit."""
    movement = await repository.get_movement(db, company_id, movement_id)
    if movement is None:
        return
    movement.invoice_id = invoice_id
    await events.publish(db, company_id, events.EventKind.MONEY)
    if movement.category_by is not CategorySetBy.OWNER:
        movement.category = MovementCategory.SUBCONTRACTOR_PAYMENT
        movement.category_by = CategorySetBy.CODE


async def unlink_from_bill(
    db: AsyncSession, company_id: uuid.UUID, movement_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    """The owner said the movement did not pay this bill: its link to it goes, and its category
    stays as it is, since it still left for that firm. No commit."""
    movement = await repository.get_movement(db, company_id, movement_id)
    if movement is None or movement.invoice_id != invoice_id:
        return
    movement.invoice_id = None
    await events.publish(db, company_id, events.EventKind.MONEY)


async def holdings_of(db: AsyncSession, company_id: uuid.UUID, currency: str) -> Holdings:
    company = (await companies.get_companies(db, [company_id]))[company_id]
    today = clock.now(company).date()
    items: list[Holding] = []
    for account in await repository.list_accounts(
        db, company_id, kinds=[AccountKind.BANK, AccountKind.CASH]
    ):
        if account.balance is None:
            continue
        converted = await fx.convert(
            db, account.balance, account.currency, currency, on=today, country=company.country
        )
        items.append(
            Holding(
                account_id=account.id,
                balance=account.balance,
                currency=account.currency,
                converted=converted.amount if converted is not None else None,
                rate=converted.rate if converted is not None else None,
                rate_on=converted.on if converted is not None else None,
                rate_source=converted.source if converted is not None else None,
            )
        )
    return Holdings(
        currency=currency,
        total=sum(
            (item.converted for item in items if item.converted is not None), Decimal(0)
        ).quantize(_CENT),
        complete=all(item.converted is not None for item in items),
        items=items,
    )


# The owner's own


async def create_account(db: AsyncSession, actor: Actor, body: AccountIn) -> AccountOut:
    """An account the owner keeps by hand. Commits."""
    actor.require(Role.OWNER)
    if body.iban is not None and any(
        held.iban == body.iban
        for held in await repository.list_accounts(db, actor.company_id, kinds=None)
    ):
        raise Conflict("The company has this IBAN already")
    account = MoneyAccount(
        company_id=actor.company_id,
        kind=body.kind,
        name=body.name,
        bank=body.bank,
        iban=body.iban,
        currency=body.currency,
        balance=body.balance,
        balance_at=utcnow() if body.balance is not None else None,
        source=TYPED,
        source_ref=str(uuid.uuid7()),
    )
    await repository.add(db, account)
    await events.publish(db, actor.company_id, events.EventKind.MONEY)
    await db.commit()
    return AccountOut.model_validate(account)


async def change_account(
    db: AsyncSession, actor: Actor, account_id: uuid.UUID, body: AccountChange
) -> AccountOut:
    """The name or the balance of an account the owner keeps; a source's account is the source's.
    Commits."""
    actor.require(Role.OWNER)
    account = await _typed_account(db, actor, account_id)
    if body.name is not None:
        account.name = body.name
    if body.balance is not None:
        account.balance, account.balance_at = body.balance, utcnow()
    await events.publish(db, actor.company_id, events.EventKind.MONEY)
    await db.commit()
    return AccountOut.model_validate(account)


async def add_movement(db: AsyncSession, actor: Actor, body: MovementIn) -> MovementOut:
    """A movement the owner says happened, on an account they keep: a bank's own account has its
    movements from the bank. Commits."""
    actor.require(Role.OWNER)
    account = await _typed_account(db, actor, body.account_id)
    movement = MoneyMovement(
        company_id=actor.company_id,
        account_id=account.id,
        source=TYPED,
        source_ref=str(uuid.uuid7()),
        booked_on=body.booked_on,
        amount=body.amount,
        currency=account.currency,
        description=body.description,
        counterparty=body.counterparty,
        category=body.category,
        category_by=CategorySetBy.OWNER if body.category is not None else None,
    )
    await repository.add(db, movement)
    await events.publish(db, actor.company_id, events.EventKind.MONEY)
    await db.commit()
    return MovementOut.model_validate(movement)


async def change_movement(
    db: AsyncSession, actor: Actor, movement_id: uuid.UUID, body: MovementChange
) -> MovementOut:
    """The owner's category, which no later sync overwrites. Commits."""
    actor.require(Role.OWNER)
    movement = await repository.get_movement(db, actor.company_id, movement_id)
    if movement is None:
        raise NotFound("No such movement")
    movement.category, movement.category_by = body.category, CategorySetBy.OWNER
    await events.publish(db, actor.company_id, events.EventKind.MONEY)
    await db.commit()
    return MovementOut.model_validate(movement)


async def _typed_account(db: AsyncSession, actor: Actor, account_id: uuid.UUID) -> MoneyAccount:
    account = await repository.get_account(db, actor.company_id, account_id)
    if account is None:
        raise NotFound("No such account")
    if account.source != TYPED:
        raise Conflict(f"This account is read from {account.source}: it changes there")
    return account


# Sources


async def store(
    company_id: uuid.UUID,
    source: str,
    accounts: Sequence[AccountRecord],
    movements: Sequence[MovementRecord],
    *,
    whole: bool = False,
) -> None:
    """What a source gives, kept by its own ids; ``whole`` when it gave every movement it has, so
    the ones it no longer gives go. A source names itself, and "typed" is the owner's alone."""
    if source == TYPED:
        raise BadRequest("The owner's own accounts are not a source's")
    async with open_session() as db:
        ids = {
            record.source_ref: await repository.upsert_account(
                db, company_id, source, asdict(record)
            )
            for record in accounts
        }
        rows = []
        for record in movements:
            values = asdict(record)
            account_ref = values.pop("account_ref")
            if account_ref not in ids:
                raise BadRequest(f"A movement of {source} names no account it gave: {account_ref}")
            rows.append(
                values
                | {
                    "account_id": ids[account_ref],
                    "category_by": CategorySetBy.CODE if record.category is not None else None,
                }
            )
        await repository.upsert_movements(db, company_id, source, rows)
        if whole:
            await repository.delete_others(
                db, company_id, source, [record.source_ref for record in movements]
            )
        await events.publish(db, company_id, events.EventKind.MONEY)
        await db.commit()


def estimate(
    invoices: Sequence[InvoiceOut], banked: Mapping[uuid.UUID, Decimal | None] | None = None
) -> tuple[list[AccountRecord], list[MovementRecord]]:
    """What the books say came in and went out: each invoice paid in whole or in part, on the day
    its payment is booked, else its due day, else its issue day, in one account per currency.
    Marked estimated: no bank named it (ADR-0026). ``banked`` is what banks' movements naming an
    invoice already account for, in its currency (None: in another, so all of it): only the rest
    is estimated."""
    banked = banked or {}
    accounts: dict[str, AccountRecord] = {}
    movements: list[MovementRecord] = []
    for invoice in invoices:
        on = invoice.payment_date or invoice.due_date or invoice.issue_date
        if (
            on is None
            or invoice.currency is None
            or invoice.total_amount is None
            or invoice.remaining_amount is None
        ):
            continue
        paid = invoice.total_amount - max(invoice.remaining_amount, Decimal(0))
        if invoice.id in banked:
            already = banked[invoice.id]
            paid = Decimal(0) if already is None else paid - already
        if paid <= 0:
            continue
        ref = f"{BOOKS}:{invoice.currency}"
        accounts.setdefault(
            ref,
            AccountRecord(
                source_ref=ref,
                kind=AccountKind.BOOKS,
                name=f"From the books ({invoice.currency})",
                currency=invoice.currency,
            ),
        )
        incoming = invoice.direction is InvoiceDirection.RECEIVABLE
        movements.append(
            MovementRecord(
                source_ref=str(invoice.id),
                account_ref=ref,
                booked_on=on,
                amount=(paid if incoming else -paid).quantize(_CENT),
                currency=invoice.currency,
                description=invoice.number,
                counterparty=invoice.party_name,
                estimated=True,
                # A sales invoice paid is a client paying; a purchase may be a subcontractor or
                # anything else the company buys, so the owner says.
                category=(
                    MovementCategory.CLIENT_RECEIPT if invoice.kind is InvoiceKind.SALES else None
                ),
                invoice_id=invoice.id,
            )
        )
    return list(accounts.values()), movements


async def sync_company(company_id: uuid.UUID) -> None:
    """The company's estimate from the books, but where a movement that is no estimate names the
    invoice, and its vault account, read again."""
    async with open_session() as db:
        invoices = await books.settled_in_part(db, company_id)
        # A bank's movement that names its invoice replaces as much of the estimate as it pays
        # (ADR-0026), and takes the owner's category of it.
        currencies = {invoice.id: invoice.currency for invoice in invoices}
        banked: dict[uuid.UUID, Decimal | None] = {}
        for invoice_id, currency, amount in await repository.paid_by_banks(db, company_id):
            if invoice_id not in currencies:
                continue
            if currency != currencies[invoice_id] or banked.get(invoice_id, 0) is None:
                banked[invoice_id] = None
            else:
                banked[invoice_id] = (banked.get(invoice_id) or Decimal(0)) + abs(amount)
        await repository.carry_owner_categories(db, company_id, BOOKS)
        await db.commit()
    accounts, movements = estimate(invoices, banked)
    await store(company_id, BOOKS, accounts, movements, whole=True)
    await _sync_vault(company_id)


async def _sync_vault(company_id: uuid.UUID) -> None:
    async with open_session() as db:
        if not await settlement.account_open(db, company_id):
            return
        payments = await settlement.settled_payments(db, company_id)
        # Each release is named by its bill: the firm it went to and the bill's number.
        bills = {
            bill.id: (bill.number, bill.party_name)
            for bill in await books.get_invoices(
                db, company_id, [payment.invoice_id for payment in payments]
            )
        }
    ref = str(company_id)
    try:
        state = await chain.company_state(company_id)
    except Exception as error:  # the balance kept is marked stale, never silently old
        async with open_session() as db:
            await repository.mark_link_error(
                db, company_id, VAULT, ref, f"The chain could not be read: {type(error).__name__}"
            )
            await events.publish(db, company_id, events.EventKind.MONEY)
            await db.commit()
        raise
    account = AccountRecord(
        source_ref=ref,
        kind=AccountKind.VAULT,
        name="Vault (test USDC)",
        currency="USDC",
        balance=chain.usdc(state.balance) if state is not None else None,
        balance_at=utcnow() if state is not None else None,
    )
    await store(
        company_id,
        VAULT,
        [account],
        [
            MovementRecord(
                source_ref=str(payment.id),
                account_ref=ref,
                booked_on=(payment.finished_at or payment.created_at).date(),
                amount=-chain.usdc(payment.usdc_units),
                currency="USDC",
                reference=payment.tx_hash,
                description=bills.get(payment.invoice_id, (None, None))[0],
                counterparty=bills.get(payment.invoice_id, (None, None))[1],
                category=MovementCategory.SUBCONTRACTOR_PAYMENT,
                invoice_id=payment.invoice_id,
            )
            for payment in payments
            if payment.usdc_units is not None
        ],
        whole=True,
    )


async def sync_all() -> int:
    """Every company with something to read; one that fails waits for the next pass."""
    async with open_session() as db:
        company_ids = sorted(
            set(await books.companies_with_settled(db))
            | set(await settlement.companies_with_open_accounts(db))
            # One whose last settled invoice was reopened still has estimates to drop.
            | set(await repository.companies_with_accounts(db, BOOKS))
        )
    done = 0
    for company_id in company_ids:
        try:
            await sync_company(company_id)
            done += 1
        except Exception as error:  # one company never stops the others
            logger.warning("reading the money of %s failed: %s", company_id, type(error).__name__)
    return done
