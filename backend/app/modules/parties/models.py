import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class PartyKind(StrEnum):
    ORGANISATION = "organisation"
    # A driver, a dispatcher, or a sole trader known by their own name.
    PERSON = "person"


class IdentifierKind(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    # VKN or TCKN in Turkey, a VAT number elsewhere.
    TAX_ID = "tax_id"
    IBAN = "iban"
    # A truck or trailer the party runs.
    PLATE = "plate"
    # The party's own name, and any other it goes by.
    NAME = "name"


class IdentifierSource(StrEnum):
    # A customer or supplier card in the company's accounting system.
    BOOKS = "books"
    OWNER = "owner"
    # Read from an item: a mail signature, a CMR, an e-irsaliye.
    EXTRACTION = "extraction"
    # Typed into a driver or dispatcher link page by the person holding it.
    LINK = "link"


class Party(CompanyScoped, Base):
    """Anyone the company deals with: clients, subcontractors, drivers, dispatchers, consignees.
    What a party is to one project is its role there (project_parties), not a property of it."""

    __tablename__ = "parties"
    __table_args__ = (
        UniqueConstraint("company_id", "source", "source_id"),
        # The target of identifiers' (company_id, party_id) key, which ties both to one company.
        UniqueConstraint("company_id", "id"),
        CheckConstraint("(source IS NULL) = (source_id IS NULL)", name="parties_source_check"),
        CheckConstraint(
            "(balance IS NULL) = (balance_currency IS NULL)",
            name="parties_balance_currency_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[PartyKind] = mapped_column(text_enum(PartyKind, "parties_kind_check"))
    # Whether a person in the company said what it is: a sync keeps their word over the books'.
    kind_chosen: Mapped[bool] = mapped_column(default=False, server_default=false())
    # The accounting card this party is synced from (its source and id), so a re-sync updates it.
    source: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(100))
    # Its account balance as the books keep it (the cari): positive when it owes the company,
    # negative when the company owes it, in ``balance_currency``, as of ``balance_on``. Read with
    # its card; null when the books give none or it has no card.
    balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    balance_currency: Mapped[str | None] = mapped_column(String(3))
    balance_on: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PartyIdentifier(CompanyScoped, Base):
    """One way a party is recognised: the identity graph linking relies on (docs/architecture/09).

    ``value`` is as it was written; ``normalised`` is what lookups compare, so "0532 123 45 67" and
    "+90 532 1234567" are one phone. The same phone or name may identify two parties (a small
    subcontractor's owner who also drives), so it is unique per party, not per company."""

    __tablename__ = "party_identifiers"
    __table_args__ = (
        UniqueConstraint("party_id", "kind", "normalised"),
        Index(None, "company_id", "kind", "normalised"),
        # One key over both columns: an identifier can only belong to a party of its own company.
        ForeignKeyConstraint(
            ["company_id", "party_id"], ["parties.company_id", "parties.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    party_id: Mapped[uuid.UUID]
    kind: Mapped[IdentifierKind] = mapped_column(
        text_enum(IdentifierKind, "party_identifiers_kind_check")
    )
    value: Mapped[str] = mapped_column(String(300))
    normalised: Mapped[str] = mapped_column(String(300))
    source: Mapped[IdentifierSource] = mapped_column(
        text_enum(IdentifierSource, "party_identifiers_source_check")
    )
    # From the books or the owner. An extracted identifier waits for the owner, and until then it
    # only suggests who sent something; it never decides a payee (ADR-0002).
    confirmed: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
