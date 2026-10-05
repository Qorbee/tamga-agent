import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class PaymentsRecorded(StrEnum):
    """How the company records a payment in its books (ADR-0022): what the Books page shows of an
    open invoice, and whether pay-when-paid counts a client invoice paid on account as paid."""

    # Against each invoice: an invoice is read alone.
    PER_INVOICE = "per_invoice"
    # Against the firm's account (its cari): the firm's balance says which invoices are paid.
    ON_ACCOUNT = "on_account"


class Policy(CompanyScoped, Base):
    """The owner's rules for paying a bill (ADR-0002). A change is a new version, never an edit,
    so a decision names the rules it was made under. A company with none pays under the
    defaults (``policy.service.DEFAULT``)."""

    __tablename__ = "policies"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("company_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    version: Mapped[int]
    # The one currency the policy's amounts are in; a bill in another is converted into it
    # through the ratio of the two currencies' mirror rates (ADR-0019).
    currency: Mapped[str] = mapped_column(String(3))
    # Above it, the owner decides; at or below it an auto-pay rule may pay, once there is one.
    approval_limit: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    # A subcontractor is paid once the client paid the job.
    pay_when_paid: Mapped[bool]
    payments_recorded: Mapped[PaymentsRecorded] = mapped_column(
        text_enum(PaymentsRecorded, "policies_payments_recorded_check"),
        server_default=PaymentsRecorded.ON_ACCOUNT.value,
    )
    # A remainder at or under this percentage of an invoice's total reads paid, with a difference
    # (a bank fee, a rate difference).
    remainder_tolerance: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="2.00")
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
