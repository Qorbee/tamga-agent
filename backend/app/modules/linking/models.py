import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class Outcome(StrEnum):
    """What the linker made of a conversation (docs/architecture/09)."""

    # Tied to a project that existed.
    LINKED = "linked"
    # A new project was proposed for it.
    PROPOSED = "proposed"
    # Two or more projects fit and nothing tells them apart: the owner chooses.
    ASKED = "asked"
    # No project fits, and it does not show a job of its own.
    UNPLACED = "unplaced"


class Placement(CompanyScoped, Base):
    """A conversation's place among the projects: whether it waits to be read, what the linker
    last made of it, and why. One row per conversation sorted as work or automated. The links
    themselves are ``project_links`` rows, suggested until the owner confirms them (ADR-0010)."""

    __tablename__ = "placements"
    __table_args__ = (
        UniqueConstraint("company_id", "thread_id"),
        # What waits, in the order it became ready.
        Index(
            "ix_placements_waiting",
            "company_id",
            "thread_changed_at",
            postgresql_where=text("waiting"),
        ),
        # The projects module's table, named in the database only.
        ForeignKeyConstraint(
            ["company_id", "project_id"], ["projects.company_id", "projects.id"], ondelete="CASCADE"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="placements_confidence_check",
        ),
        CheckConstraint("(outcome IS NULL) = (placed_at IS NULL)", name="placements_placed_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # The conversations module's table, named in the database only.
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    # New mail, or a new sorting, since it was last read.
    waiting: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # The thread's change this row last took note of (threads.changed_at).
    thread_changed_at: Mapped[datetime]
    # Null until it was read once.
    outcome: Mapped[Outcome | None] = mapped_column(text_enum(Outcome, "placements_outcome_check"))
    # What decided: "keys" (a number it shares with one project), "thread" (new mail in a
    # conversation already placed), "model:linker", or "unanswered" when the model gave no answer.
    placed_by: Mapped[str | None] = mapped_column(String(40))
    # The project it was linked to, or the one proposed for it.
    project_id: Mapped[uuid.UUID | None]
    # The projects the owner is asked to choose between.
    alternatives: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=text("'{}'")
    )
    # One line of why, for the owner to read.
    reason: Mapped[str] = mapped_column(String(500), default="", server_default="")
    confidence: Mapped[float | None]
    # The mails the linker relied on.
    cited: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=text("'{}'")
    )
    # What the model read: of the conversation, who the company deals with in it and what that
    # firm is to it in this job (party, party_role); of a job it proposed a project for, also
    # client, subcontractor, origin, destination, cargo.
    reading: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # How many mails it held when it was read, and the newest of them. A change that brings no
    # mail (a copy stored from a second mailbox) is not read again, and a later mail soon after
    # follows the conversation without a model reading it again.
    mails: Mapped[int] = mapped_column(default=0, server_default="0")
    last_mail_at: Mapped[datetime | None]
    # How many of its files had been read then: one read since brings facts it has not seen.
    files_read: Mapped[int] = mapped_column(default=0, server_default="0")
    placed_at: Mapped[datetime | None]
    # Times the model could not be reached for it, and when it last could not: after three, it is
    # tried once an hour.
    failures: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    failed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
