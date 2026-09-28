import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.items import ItemKind
from app.platform.tenancy import CompanyScoped


class Fact(CompanyScoped, Base):
    """A typed value read from an item: a plate, an ETTN, an order number, a place, an amount.

    ``kind`` is open: packs add their own (a CMR number, a Turkish plate). ``normalised`` is the
    form the candidate search compares, so the index is on (company, kind, normalised). A link
    cites the ids of the facts it relied on (ADR-0010)."""

    __tablename__ = "facts"
    __table_args__ = (
        # Reading an item again finds the same facts: one row each, whichever extractor ran again.
        UniqueConstraint("company_id", "item_kind", "item_id", "kind", "normalised"),
        Index(None, "company_id", "kind", "normalised"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="facts_confidence_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    item_kind: Mapped[ItemKind] = mapped_column(text_enum(ItemKind, "facts_item_kind_check"))
    item_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)
    normalised: Mapped[str] = mapped_column(String(300))
    confidence: Mapped[float]
    # What read it: "regex", "ubl", "model:<agent>", "owner".
    extractor: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
