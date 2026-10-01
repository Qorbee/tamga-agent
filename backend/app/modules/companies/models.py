import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import CheckConstraint, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.language import Language


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
    __table_args__ = (
        # Only a replay has a cursor, and a replay always has one: clock.now reads it (ADR-0009).
        CheckConstraint(
            "(mode = 'replay') = (replay_cursor IS NOT NULL)", name="companies_replay_cursor_check"
        ),
        # Only a sandbox's clock runs ahead; a pilot's business time is the wall clock.
        CheckConstraint(
            "kind = 'sandbox' OR clock_offset = interval '0'", name="companies_clock_offset_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[CompanyKind] = mapped_column(text_enum(CompanyKind, "companies_kind_check"))
    mode: Mapped[CompanyMode] = mapped_column(text_enum(CompanyMode, "companies_mode_check"))
    # The country pack whose documents, identifiers and rules apply (app/packs/<country>).
    country: Mapped[str] = mapped_column(String(2), default="tr")
    # The language Tamga writes in for the company: the names it gives, its reasons, what it says
    # a file is. The chat answers in the language it is written to.
    language: Mapped[Language] = mapped_column(
        text_enum(Language, "companies_language_check"), default=Language.EN, server_default="en"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Sandboxes only: when the cleanup deletes the company and everything it owns.
    expires_at: Mapped[datetime | None]
    # Sandboxes only: how far the simulated clock is ahead of the wall clock ("skip two days").
    clock_offset: Mapped[timedelta] = mapped_column(
        default=timedelta(0), server_default=text("interval '0'")
    )
    # Replays only: how far the history has been fed. Nothing in the company may see past it.
    replay_cursor: Mapped[datetime | None]
