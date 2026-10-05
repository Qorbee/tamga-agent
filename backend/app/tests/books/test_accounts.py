"""Reading a firm's account in the books (ADR-0022): its balance laid on its open invoices
newest first, the oldest left uncovered reading paid on account."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from app.modules.books import accounts
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.schemas import InvoiceOut, Settled
from app.modules.policy.models import PaymentsRecorded

ON = date(2026, 10, 3)


def invoice(
    issued: date,
    left: str,
    *,
    total: str | None = None,
    currency: str = "EUR",
    direction: InvoiceDirection = InvoiceDirection.PAYABLE,
) -> InvoiceOut:
    return InvoiceOut(
        id=uuid.uuid4(),
        source="books",
        direction=direction,
        kind=InvoiceKind.PURCHASE if direction is InvoiceDirection.PAYABLE else InvoiceKind.SALES,
        number=None,
        e_document_id=None,
        party_id=None,
        party_name=None,
        issue_date=issued,
        due_date=None,
        payment_date=None,
        currency=currency,
        net_amount=None,
        tax_amount=None,
        total_amount=Decimal(total or left),
        remaining_amount=Decimal(left),
        cancelled=False,
        status=None,
        description=None,
        same_as_id=None,
        synced_at=datetime(2026, 10, 3, tzinfo=UTC),
    )


def owed(balance: str, invoices: list[InvoiceOut]) -> list[Decimal | None]:
    readings = accounts.lay(accounts.Balance(Decimal(balance), "EUR", ON), invoices)
    return [readings[each.id].owed for each in invoices]


def test_the_balance_is_laid_newest_first_the_oldest_paid_on_account() -> None:
    old, middle, new = (
        invoice(date(2026, 7, 1), "1000.00"),
        invoice(date(2026, 8, 1), "1000.00"),
        invoice(date(2026, 9, 1), "1000.00"),
    )

    # The company owes the firm 1500: the newest is owed, half of the one before, not the oldest.
    assert owed("-1500.00", [old, middle, new]) == [
        Decimal("0.00"),
        Decimal("500.00"),
        Decimal("1000.00"),
    ]


def test_each_side_of_an_account_is_read_on_its_own() -> None:
    bought = invoice(date(2026, 8, 1), "800.00")
    sold = invoice(date(2026, 8, 1), "300.00", direction=InvoiceDirection.RECEIVABLE)

    # The firm owes the company 300 net: what it sold the company was settled by offset.
    assert owed("300.00", [bought, sold]) == [Decimal("0.00"), Decimal("300.00")]


def test_the_account_cannot_speak_for_another_currency_or_a_later_invoice() -> None:
    lira = invoice(date(2026, 8, 1), "5000.00", currency="TRY")
    later = invoice(ON, "400.00")
    held = invoice(date(2026, 8, 1), "400.00")

    assert owed("-400.00", [lira, later, held]) == [None, None, Decimal("400.00")]
    unread = accounts.lay(accounts.Balance(Decimal("-400.00"), "EUR", None), [held])
    assert unread[held.id].owed is None


def test_what_an_open_invoice_reads_as() -> None:
    paid = accounts.Account(Decimal("0.00"), ON)
    on_account, per_invoice = PaymentsRecorded.ON_ACCOUNT, PaymentsRecorded.PER_INVOICE
    bill = invoice(date(2026, 8, 1), "1000.00")
    fee = invoice(date(2026, 8, 1), "15.00", total="1000.00")
    tolerance = Decimal("2.00")

    assert accounts.settled(bill, paid, on_account, tolerance) is Settled.PAID_ON_ACCOUNT
    assert accounts.settled(bill, paid, per_invoice, tolerance) is Settled.OPEN
    assert accounts.settled(bill, None, on_account, tolerance) is Settled.UNKNOWN
    assert accounts.settled(fee, None, per_invoice, tolerance) is Settled.PAID_WITH_DIFFERENCE
    closed = bill.model_copy(update={"remaining_amount": Decimal("0.00")})
    assert accounts.settled(closed, paid, on_account, tolerance) is None
