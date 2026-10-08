import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class MemoryKind(StrEnum):
    """What an entry of the company's memory is (ADR-0024)."""

    # A word the company uses, and what it means there.
    TERM = "term"
    # Another name of a firm, a place or a route, as the company or its mail calls it.
    ALIAS = "alias"
    # Who someone is to the company.
    PERSON = "person"
    # How the owner wants a thing done or said.
    PREFERENCE = "preference"
    # How the company works.
    PRACTICE = "practice"


class MemoryState(StrEnum):
    # Drafted in a chat: no agent reads it until the owner confirms it.
    PROPOSED = "proposed"
    # The owner wrote or confirmed it: every agent of the company reads it, as data.
    HELD = "held"
    # The owner took it back: no agent reads it.
    RETIRED = "retired"


class MemorySource(StrEnum):
    # Drafted by the chat's `remember`, in the chat and message it names.
    CHAT = "chat"
    # Written by the owner in Settings.
    SETTINGS = "settings"


class MemoryEntry(CompanyScoped, Base):
    """One thing the company's owner taught Tamga (ADR-0024). It holds only on the owner's
    action: written in Settings, or a chat's draft the owner confirmed."""

    __tablename__ = "memory_entries"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index(None, "company_id", "state"),
        Index(
            "ix_memory_entries_search_text",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    kind: Mapped[MemoryKind] = mapped_column(text_enum(MemoryKind, "memory_entries_kind_check"))
    text: Mapped[str] = mapped_column(String(500))
    # Folded for search (`platform/search.py`).
    search_text: Mapped[str] = mapped_column(Text)
    # The firm or the project it is about, if one.
    party_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    source: Mapped[MemorySource] = mapped_column(
        text_enum(MemorySource, "memory_entries_source_check")
    )
    # Where a chat drafted it.
    chat_id: Mapped[uuid.UUID | None]
    state: Mapped[MemoryState] = mapped_column(text_enum(MemoryState, "memory_entries_state_check"))
    # Who wrote it: the user who typed it, or None for Tamga's draft.
    written_by: Mapped[uuid.UUID | None]
    # Who made it hold, and when: always the owner.
    confirmed_by: Mapped[uuid.UUID | None]
    confirmed_at: Mapped[datetime | None]
    # When an agent was last given it.
    last_used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
