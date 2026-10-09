import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class FxRate(Base):
    """One currency's official reference rate on one day, as its source published it (ADR-0028):
    how many units of the source's base one unit of the currency is worth. Public data, the same
    for every company, so it carries no company (ADR-0003, `GLOBAL_TABLES`)."""

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("source", "currency", "kind", "on"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # The registered source's name, e.g. a central bank's.
    source: Mapped[str] = mapped_column(String(20))
    # The currency the rate is counted in, the source's own.
    base: Mapped[str] = mapped_column(String(3))
    currency: Mapped[str] = mapped_column(String(3))
    # Which of the source's rates it is: "buying", "selling", or "reference" for a source that
    # publishes one rate.
    kind: Mapped[str] = mapped_column(String(10))
    # The day the source published it for.
    on: Mapped[date]
    rate: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    read_at: Mapped[datetime] = mapped_column(server_default=func.now())
