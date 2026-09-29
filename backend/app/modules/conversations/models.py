import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Computed,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class Channel(StrEnum):
    """Where an item came from (docs/architecture/06)."""

    EMAIL = "email"
    UPLOAD = "upload"
    SHARE = "share"
    LINK = "link"
    SIMULATED = "simulated"
    WHATSAPP = "whatsapp"


class Direction(StrEnum):
    INBOUND = "inbound"
    # Written by the company: a mail in its own Sent folder, or from one of its own addresses.
    OUTBOUND = "outbound"


class Message(CompanyScoped, Base):
    """One item from a channel, stored once however many folders or re-reads it turns up in. Its
    text is data, never instructions (root AGENTS.md, invariant 3)."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("company_id", "content_hash"),
        Index("ix_messages_company_id_thread_key", "company_id", "thread_key"),
        # A reply finds its parent's thread by the parent's Message-ID.
        Index("ix_messages_company_id_rfc_message_id", "company_id", "rfc_message_id"),
        # Search matches typed terms inside these (LIKE '%…%'), which trigrams serve.
        Index(
            "ix_messages_search_text",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_messages_search_compact",
            "search_compact",
            postgresql_using="gin",
            postgresql_ops={"search_compact": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    channel: Mapped[Channel] = mapped_column(text_enum(Channel, "messages_channel_check"))
    direction: Mapped[Direction] = mapped_column(text_enum(Direction, "messages_direction_check"))
    # A mail's identity is the SHA-256 of its raw bytes (ADR-0008): Message-ID is optional and not
    # always unique, so it is threading data, never the key.
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    # The original bytes in storage, for re-reading and the replay.
    raw_key: Mapped[str | None] = mapped_column(String(120))
    rfc_message_id: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # The conversation it belongs to: the first Message-ID in its References (the thread's first
    # mail), else its parent's thread, else its own Message-ID. Copies of one mail in several
    # mailboxes share it.
    thread_key: Mapped[str] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(320))
    sender_name: Mapped[str | None] = mapped_column(String(200))
    recipients: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    subject: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, default="")
    # What a search looks in, folded (`platform/search.py`): subject, sender, recipients, the names
    # of the files beside the text, and the text. Words are matched from their start in it, and a
    # term with a digit (a plate, a number) anywhere in its copy without spaces.
    search_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    search_compact: Mapped[str] = mapped_column(
        Text, Computed("replace(search_text, ' ', '')", persisted=True)
    )
    # The message's own time (its Date header, else the server's arrival time), which the replay
    # orders by; received_at is when Tamga stored it.
    sent_at: Mapped[datetime] = mapped_column(index=True)
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MessageLocation(CompanyScoped, Base):
    """A place a mail was seen. The same mail in Inbox and in a project folder has two."""

    __tablename__ = "message_locations"
    __table_args__ = (UniqueConstraint("company_id", "mailbox", "folder", "uidvalidity", "uid"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    mailbox: Mapped[str] = mapped_column(String(320))
    folder: Mapped[str] = mapped_column(String(500))
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    uid: Mapped[int] = mapped_column(BigInteger)


class MailFolder(CompanyScoped, Base):
    """A folder's poll cursor: nothing on the server records what Tamga has read (ADR-0008)."""

    __tablename__ = "mail_folders"
    __table_args__ = (UniqueConstraint("company_id", "mailbox", "folder"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    mailbox: Mapped[str] = mapped_column(String(320))
    folder: Mapped[str] = mapped_column(String(500))
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    last_uid: Mapped[int] = mapped_column(BigInteger)
    polled_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MessageAttachment(CompanyScoped, Base):
    __tablename__ = "message_attachments"
    __table_args__ = (UniqueConstraint("message_id", "document_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # The name in this mail, which can differ from the document's first name.
    filename: Mapped[str] = mapped_column(String(300))
    # Shown inside the mail's HTML body by its Content-ID (a signature logo, a pasted screenshot).
    inline: Mapped[bool] = mapped_column(default=False, server_default=false())
