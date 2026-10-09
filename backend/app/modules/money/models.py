import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class AccountKind(StrEnum):
    """Where a company's money is held (ADR-0026)."""

    BANK = "bank"
    CASH = "cash"
    # The company's test-USDC account on PolicyVault: a mirror, never counted as money.
    VAULT = "vault"
    # What the books say came in and went out, with no bank named: estimates, one per currency.
    BOOKS = "books"


class MovementCategory(StrEnum):
    CLIENT_RECEIPT = "client_receipt"
    SUBCONTRACTOR_PAYMENT = "subcontractor_payment"
    SALARY = "salary"
    TAX = "tax"
    BANK_FEE = "bank_fee"
    LOAN = "loan"
    # Between two of the company's own accounts: counted in neither total.
    OWN_TRANSFER = "own_transfer"
    # A partner or a relative taking money out, or putting it in.
    PARTNER_WITHDRAWAL = "partner_withdrawal"
    OTHER = "other"


class CategorySetBy(StrEnum):
    # Sure from the source: its own code, a book estimate's invoice, an own transfer.
    CODE = "code"
    # The owner's word, which a sync never overwrites.
    OWNER = "owner"


class MoneyAccount(CompanyScoped, Base):
    """A bank account, a cash box, the vault, or the books' estimate in one currency. A bank
    account is one account by its IBAN, whichever source reads it (ADR-0026)."""

    __tablename__ = "money_accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "source", "source_ref"),
        Index(
            "money_accounts_company_id_iban_key",
            "company_id",
            "iban",
            unique=True,
            postgresql_where=text("iban IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    kind: Mapped[AccountKind] = mapped_column(text_enum(AccountKind, "money_account_kind_check"))
    name: Mapped[str] = mapped_column(String(120))
    bank: Mapped[str | None] = mapped_column(String(120))
    iban: Mapped[str | None] = mapped_column(String(34))
    currency: Mapped[str] = mapped_column(String(4))
    # What the source or the owner last said it holds, and when; None where nobody knows.
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    balance_at: Mapped[datetime | None]
    # The source that reads it now ("typed" for the owner's own), and its id there.
    source: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str] = mapped_column(String(100))
    # Why the source could not read it last time; the balance is stale while this is set.
    link_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MoneyMovement(CompanyScoped, Base):
    """Money in (positive) or out (negative) of one account. Unique by its source and the source's
    own id, so two equal movements on one day stay two (ADR-0026)."""

    __tablename__ = "money_movements"
    __table_args__ = (
        UniqueConstraint("company_id", "source", "source_ref"),
        Index("money_movements_company_id_booked_on_idx", "company_id", "booked_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("money_accounts.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str] = mapped_column(String(100))
    booked_on: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(4))
    # As the source wrote it: shown and searched, never followed.
    description: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(String(300))
    counterparty_iban: Mapped[str | None] = mapped_column(String(34))
    counterparty_tax_id: Mapped[str | None] = mapped_column(String(20))
    reference: Mapped[str | None] = mapped_column(String(100))
    # An estimate from the books, until a bank's movement replaces it.
    estimated: Mapped[bool] = mapped_column(default=False)
    category: Mapped[MovementCategory | None] = mapped_column(
        text_enum(MovementCategory, "money_movement_category_check", length=24)
    )
    category_by: Mapped[CategorySetBy | None] = mapped_column(
        text_enum(CategorySetBy, "money_movement_category_by_check")
    )
    # The invoice it pays or collects, where that is sure.
    invoice_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
