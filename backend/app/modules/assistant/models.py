import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class Origin(StrEnum):
    """Who began a chat, or a run in one (ADR-0018). Either way it is an ordinary chat."""

    OWNER = "owner"
    TAMGA = "tamga"


class MessageKind(StrEnum):
    """What a row of a chat is: a message of the model layer, a card code put there, or the
    notes a compaction wrote of the chat up to a place."""

    MODEL = "model"
    CARD = "card"
    NOTES = "notes"


class CardKind(StrEnum):
    """The cards code puts under a message of Tamga's, as a client knows them: what the card
    is about says which record its id names."""

    # A project the linker proposes: the project's id.
    PROJECT_PROPOSAL = "project_proposal"
    # A conversation the linker could not choose a project for: its placement's id.
    CONVERSATION_QUESTION = "conversation_question"
    # A bill that waits for the owner's approval: its decision's id. Chats from before Oct 13
    # hold it; Tamga now writes about the bills that wait together.
    DECISION_APPROVAL = "decision_approval"
    # The bills that waited for the owner's approval when Tamga wrote, together: the first
    # one's decision id, and every one's id and evaluation.
    DECISION_BATCH = "decision_batch"
    # The morning brief: where the money stands as the card is read, the day's id.
    MORNING_BRIEF = "morning_brief"
    # A week's lessons for the owner to keep or skip (ADR-0032): the week's id, the drafted
    # entries' ids and those of entries asked to be forgotten.
    LEARNED = "learned"


class Chat(CompanyScoped, Base):
    """One conversation between a company's people and Tamga. It is bound to no subject: code
    only keeps which records it holds (``chat_records``), to find where a finding belongs."""

    __tablename__ = "chats"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        Index(None, "company_id", "last_message_at"),
        # One digest a company a day.
        Index(
            "ix_chats_digest_on",
            "company_id",
            "digest_on",
            unique=True,
            postgresql_where=text("digest_on IS NOT NULL"),
        ),
        Index(
            "ix_chats_search_text",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    origin: Mapped[Origin] = mapped_column(text_enum(Origin, "chats_origin_check"))
    # Empty until its first message names it.
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # The day whose digest it is (ADR-0018, points 8 and 9): what Tamga found past the day's new
    # chats, as cards with no model run. Null for every other chat.
    digest_on: Mapped[date | None]
    opened_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archived_at: Mapped[datetime | None]
    # What its next run would be sent, as the model counted its last one: the last request's
    # input and its answer.
    context_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    # When its last message was written; null while it holds none.
    last_message_at: Mapped[datetime | None]
    # When the run it has in flight began; null when it has none. A chat takes one run at a
    # time, whichever process makes it.
    running_since: Mapped[datetime | None]
    # A few lines on what it was about, written once it has gone quiet (ADR-0025): what a later
    # chat's search finds it by. Empty until then; written again after new messages.
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Its title and summary folded for search (`platform/search.py`).
    search_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    # When the summary was written; behind ``last_message_at``, it is due again.
    summarized_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChatRead(CompanyScoped, Base):
    """How far one user has read one chat. Unread is per user: one member opening a chat never
    clears it for the owner."""

    __tablename__ = "chat_reads"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id"),
        ForeignKeyConstraint(
            ["company_id", "chat_id"], ["chats.company_id", "chats.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    chat_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    read_at: Mapped[datetime]


class ChatMessage(CompanyScoped, Base):
    """One message of a run, as Pydantic AI holds it (a request with what the user wrote and
    what tools returned, or the model's response), in the order they were written. Or a card:
    a record code shows in the chat (a proposed project, a conversation to place), by its kind
    and id, which no model writes and no model is sent. Or notes: the summary a compaction
    wrote, and the place it covers the chat through, which runs are sent in place of the
    messages up to there."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "position"),
        ForeignKeyConstraint(
            ["company_id", "chat_id"], ["chats.company_id", "chats.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    chat_id: Mapped[uuid.UUID]
    position: Mapped[int]
    kind: Mapped[MessageKind] = mapped_column(
        text_enum(MessageKind, "chat_messages_kind_check"),
        default=MessageKind.MODEL,
        server_default=MessageKind.MODEL,
    )
    # The run that wrote it, and who began that run.
    run_id: Mapped[uuid.UUID]
    began_by: Mapped[Origin] = mapped_column(text_enum(Origin, "chat_messages_began_by_check"))
    # The user whose run it was; null for a run Tamga began, and once that user is gone.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    message: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChatRecord(CompanyScoped, Base):
    """A record a chat holds (a project, a conversation Tamga could not place): one Tamga raised
    there, or one a run opened or changed by its id. An index for finding context, never a
    limit on what the chat may be about."""

    __tablename__ = "chat_records"
    __table_args__ = (
        UniqueConstraint("chat_id", "record_kind", "record_id"),
        ForeignKeyConstraint(
            ["company_id", "chat_id"], ["chats.company_id", "chats.id"], ondelete="CASCADE"
        ),
        # Which chat most recently held a record.
        Index(None, "company_id", "record_kind", "record_id", "touched_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    chat_id: Mapped[uuid.UUID]
    record_kind: Mapped[str] = mapped_column(String(40))
    record_id: Mapped[uuid.UUID]
    touched_at: Mapped[datetime]
    # When Tamga wrote about the record here on its own; null when only a run opened it.
    raised_at: Mapped[datetime | None]


class ChatRun(CompanyScoped, Base):
    """What a run of the assistant asked of the model: the ledger its day's budget is counted
    from (CHAT_REQUESTS_PER_DAY). A run is entered with the most it may ask before the model is
    called, and set to what it asked when it ends. It is not tied to its chat, so deleting a
    chat does not give its requests back."""

    __tablename__ = "chat_runs"
    __table_args__ = (Index(None, "company_id", "ran_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    began_by: Mapped[Origin] = mapped_column(text_enum(Origin, "chat_runs_began_by_check"))
    requests: Mapped[int] = mapped_column(SmallInteger)
    ran_at: Mapped[datetime]
    # Why Tamga took a turn on its own, in code's words ("3 bills wait for you"): shown above
    # what it wrote. None for a run someone asked for.
    reason: Mapped[str | None] = mapped_column(String(200))
