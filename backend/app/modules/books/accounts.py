"""Reading a firm's account in the books (ADR-0022): which of its open invoices its balance still
says are owed. A company that records a payment against the firm's account (its cari) rather
than an invoice leaves the invoice open in the books though it was paid; the balance knows.

What the balance says is owed on one side (to the company when positive, by it when negative)
is laid on that side's open invoices newest first, each taking at most what is left of it: the
oldest are the ones a payment on account paid. Only invoices in the balance's currency and
issued before the balance was read are laid on; for any other the account cannot speak.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.modules.books.models import InvoiceDirection
from app.modules.books.schemas import InvoiceOut, Settled
from app.modules.policy.models import PaymentsRecorded

_ZERO: Final = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Account:
    """What a firm's account says of one of its open invoices: how much of it is owed, or None
    when the account cannot speak for it; and the day the balance is of."""

    owed: Decimal | None
    on: date | None


@dataclass(frozen=True, slots=True)
class Balance:
    amount: Decimal | None
    currency: str | None
    on: date | None


def lay(balance: Balance, invoices: Sequence[InvoiceOut]) -> dict[uuid.UUID, Account]:
    """The account's reading of each of one firm's open invoices."""
    amount = balance.amount
    readings = {invoice.id: Account(None, balance.on) for invoice in invoices}
    if amount is None or balance.on is None:
        return readings
    for side, owed in (
        (InvoiceDirection.RECEIVABLE, max(amount, _ZERO)),
        (InvoiceDirection.PAYABLE, max(-amount, _ZERO)),
    ):
        laid_on = sorted(
            (
                invoice
                for invoice in invoices
                if invoice.direction is side and _spoken(invoice, balance)
            ),
            key=lambda invoice: (invoice.issue_date, invoice.id),
            reverse=True,
        )
        for invoice in laid_on:
            left = invoice.remaining_amount or _ZERO
            readings[invoice.id] = Account(min(left, owed), balance.on)
            owed -= min(left, owed)
    return readings


def _spoken(invoice: InvoiceOut, balance: Balance) -> bool:
    """Whether the balance holds the invoice: in its currency, and issued before it was read
    (an invoice of the day it was read may have come after)."""
    return (
        invoice.currency == balance.currency
        and invoice.issue_date is not None
        and balance.on is not None
        and invoice.issue_date < balance.on
        and invoice.remaining_amount is not None
    )


def settled(
    invoice: InvoiceOut,
    account: Account | None,
    recorded: PaymentsRecorded,
    tolerance: Decimal,
) -> Settled | None:
    """What an invoice the books show open reads as, under how the company records payments and
    the remainder its policy lets pass; None for one the books show paid or cancelled."""
    left = invoice.remaining_amount
    if invoice.cancelled or left is None or left <= 0:
        return None
    if within(invoice, tolerance):
        return Settled.PAID_WITH_DIFFERENCE
    if recorded is PaymentsRecorded.PER_INVOICE:
        return Settled.OPEN
    if account is None or account.owed is None:
        return Settled.UNKNOWN
    return Settled.PAID_ON_ACCOUNT if account.owed <= 0 else Settled.OPEN


def within(invoice: InvoiceOut, tolerance: Decimal) -> bool:
    """Whether what is left of an invoice is at or under ``tolerance`` percent of its total."""
    left, total = invoice.remaining_amount, invoice.total_amount
    return (
        left is not None
        and left > 0
        and total is not None
        and total > 0
        and left <= total * tolerance / 100
    )
