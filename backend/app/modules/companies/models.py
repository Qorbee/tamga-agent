import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum


class CompanyKind(StrEnum):
    """Whose data the company holds (ADR-0003)."""

    PILOT = "pilot"
    DEMO = "demo"
    SANDBOX = "sandbox"


class CompanyMode(StrEnum):
    """What Tamga may do for the company (ADR-0009)."""

    REPLAY = "replay"
    SHADOW = "shadow"
    LIVE = "live"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[CompanyKind] = mapped_column(text_enum(CompanyKind, "companies_kind_check"))
    mode: Mapped[CompanyMode] = mapped_column(text_enum(CompanyMode, "companies_mode_check"))
    # The country pack whose documents, identifiers and rules apply (app/packs/<country>).
    country: Mapped[str] = mapped_column(String(2), default="tr")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Sandboxes only: when the cleanup deletes the company and everything it owns.
    expires_at: Mapped[datetime | None]
