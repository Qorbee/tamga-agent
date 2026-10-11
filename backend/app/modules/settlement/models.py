import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.policy.models import PaymentMethod
from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class AccountState(StrEnum):
    # Its wallets are made, and its registration and deposit are on their way to the chain.
    OPENING = "opening"
    # Registered on the vault and funded: it can pay.
    OPEN = "open"
    # A step failed for a reason that trying again will not change; ``failure`` says which.
    FAILED = "failed"


class VaultAccount(CompanyScoped, Base):
    """A company on PolicyVault (ADR-0015): the owner key that signs its changes (ADR-0016), the
    caps and timelock it was registered with, its deposit, and the transactions that opened it.
    What the vault holds now is read from the chain, never from here."""

    __tablename__ = "vault_accounts"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    state: Mapped[AccountState] = mapped_column(
        text_enum(AccountState, "vault_accounts_state_check")
    )
    # A Circle developer-controlled wallet of the company's own, which never sends a transaction.
    owner_wallet_id: Mapped[str | None] = mapped_column(String(64))
    owner_address: Mapped[str | None] = mapped_column(String(42))
    timelock_seconds: Mapped[int]
    # In USDC's smallest units (6 decimals).
    per_payment_cap: Mapped[int] = mapped_column(BigInteger)
    daily_cap: Mapped[int] = mapped_column(BigInteger)
    deposit: Mapped[int] = mapped_column(BigInteger)
    # Each step's transaction, by step: the idempotency key it was sent with, Circle's id and the
    # chain's hash, written before and after it is sent, so a step tried again is the same one.
    transactions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    failure: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    opened_at: Mapped[datetime | None]


class PayeeWallet(CompanyScoped, Base):
    """The wallet Tamga holds for a party the company pays: a Circle developer-controlled wallet
    that only receives, put on the company's payee list on the vault."""

    __tablename__ = "payee_wallets"
    __table_args__ = (
        UniqueConstraint("company_id", "party_id"),
        ForeignKeyConstraint(
            ["company_id", "party_id"], ["parties.company_id", "parties.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    party_id: Mapped[uuid.UUID]
    circle_wallet_id: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(String(42))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PaymentState(StrEnum):
    # Its idempotency key is written; the executor's call is on its way, or will be sent again
    # with the same key.
    SUBMITTING = "submitting"
    SETTLED = "settled"
    # The vault refused it; ``failure`` says why.
    FAILED = "failed"
    # A replay company's: recorded, never sent (ADR-0009).
    WOULD_RELEASE = "would_release"


class PaymentKind(StrEnum):
    # What settles the bill.
    PAYMENT = "payment"
    # Test USDC released beside a bank transfer in shadow mode: a record, never the bill's
    # payment (ADR-0027).
    MIRROR = "mirror"


class Payment(CompanyScoped, Base):
    """One payment of an approved evaluation of a decision (05, Data): a release the executor
    makes from the vault, or a bank transfer the owner made. A release's idempotency key is
    written before the call is sent, so a call sent again is the same Circle transaction; the
    vault pays the bill's key once, so no bill is paid twice."""

    __tablename__ = "payments"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "decision_id", "evaluation", "kind"),
        UniqueConstraint("idempotency_key"),
        # One movement pays one bill.
        UniqueConstraint("company_id", "movement_id"),
        CheckConstraint(
            "(method <> 'vault_usdc' AND kind <> 'mirror') OR (payee IS NOT NULL AND "
            "usdc_units IS NOT NULL AND payment_key IS NOT NULL AND idempotency_key IS NOT NULL)",
            name="payments_vault_fields_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    decision_id: Mapped[uuid.UUID]
    evaluation: Mapped[int]
    invoice_id: Mapped[uuid.UUID]
    kind: Mapped[PaymentKind] = mapped_column(
        text_enum(PaymentKind, "payments_kind_check"), server_default=PaymentKind.PAYMENT.value
    )
    method: Mapped[PaymentMethod] = mapped_column(
        text_enum(PaymentMethod, "payments_method_check", length=20),
        server_default=PaymentMethod.VAULT_USDC.value,
    )
    # What left the company's account, in its own currency: the test USDC released, the bank
    # movement's amount, or what the owner said they paid.
    pay_currency: Mapped[str | None] = mapped_column(String(4))
    pay_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    paid_on: Mapped[date | None]
    # The proof of a bank transfer: the movement matched to it, or the owner who marked it paid.
    movement_id: Mapped[uuid.UUID | None]
    marked_by: Mapped[uuid.UUID | None]
    # The vault's, for a vault method or a mirror (the CHECK above).
    payee: Mapped[str | None] = mapped_column(String(42))
    usdc_units: Mapped[int | None] = mapped_column(BigInteger)
    # The bill's key on the vault, as sent: 0x and 64 hex digits.
    payment_key: Mapped[str | None] = mapped_column(String(66))
    idempotency_key: Mapped[uuid.UUID | None]
    state: Mapped[PaymentState] = mapped_column(
        text_enum(PaymentState, "payments_state_check", length=20)
    )
    circle_transaction_id: Mapped[str | None] = mapped_column(String(64))
    tx_hash: Mapped[str | None] = mapped_column(String(66))
    failure: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
