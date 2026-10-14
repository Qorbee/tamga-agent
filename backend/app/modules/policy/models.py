import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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


class PaymentMethod(StrEnum):
    """How a bill is paid (ADR-0027)."""

    # Test USDC from the company's account on PolicyVault, released by the executor.
    VAULT_USDC = "vault_usdc"
    # The owner pays in the bank; Tamga records it.
    BANK_TRANSFER = "bank_transfer"


class Policy(CompanyScoped, Base):
    """The owner's rules for paying a bill (ADR-0002). A change is a new version, never an edit,
    so a decision names the rules it was made under. A company with none pays under the
    defaults (``policy.service.DEFAULT``)."""

    __tablename__ = "policies"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "version"),
        CheckConstraint("payment_weekday BETWEEN 0 AND 6", name="policies_payment_weekday_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    version: Mapped[int]
    # The currency the policy's amounts are written in; a bill in another is converted into it at
    # the official reference rate (ADR-0028).
    currency: Mapped[str] = mapped_column(String(3))
    # Above it, the owner decides; at or below it an auto-pay rule may pay, once there is one.
    approval_limit: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    # A subcontractor is paid once the client paid the job.
    pay_when_paid: Mapped[bool]
    payments_recorded: Mapped[PaymentsRecorded] = mapped_column(
        text_enum(PaymentsRecorded, "policies_payments_recorded_check"),
        server_default=PaymentsRecorded.PER_INVOICE.value,
    )
    # A remainder at or under this percentage of an invoice's total reads paid, with a difference
    # (a bank fee, a rate difference).
    remainder_tolerance: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="2.00")
    # The ways the company pays, and the one proposed unless a payee can take only another
    # (ADR-0027). No vault method: crypto is off.
    methods: Mapped[list[str]] = mapped_column(ARRAY(String(20)), server_default="{vault_usdc}")
    default_method: Mapped[PaymentMethod] = mapped_column(
        text_enum(PaymentMethod, "policies_default_method_check", length=20),
        server_default=PaymentMethod.VAULT_USDC.value,
    )
    # The reserve rule in force, a copy of it (``reserve.ReserveRule``): a preset changed later
    # never changes what this version decided under.
    reserve: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The owner's own reserve rules, each duplicated from a preset and changed.
    reserve_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # The day of the week the owner likes to pay on, Monday 0; None for any day. A preference
    # for gathering payments, never a rule: nothing is held for the day.
    payment_weekday: Mapped[int | None]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DraftState(StrEnum):
    # Drafted from the owner's words in a chat: nothing reads it until the owner confirms it.
    DRAFT = "draft"
    # Confirmed: it made a new version of the policy.
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class PolicyDraft(CompanyScoped, Base):
    """A change to the policy drafted from the owner's words in a chat (ADR-0002: agents
    propose): only the fields it changes, applied to the version in force when the owner
    confirms it, so a change made meanwhile elsewhere is kept."""

    __tablename__ = "policy_drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # A ``PolicyChange``: only the fields it changes.
    change: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The owner's words it was drafted from, as the chat model quoted them.
    said: Mapped[str] = mapped_column(String(500))
    state: Mapped[DraftState] = mapped_column(
        text_enum(DraftState, "policy_drafts_state_check", length=10),
        server_default=DraftState.DRAFT.value,
    )
    # The version in force when it was drafted, and the one confirming it made.
    based_on: Mapped[int]
    made_version: Mapped[int | None]
    chat_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
