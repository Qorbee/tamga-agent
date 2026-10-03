import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

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
