import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.isolated import Reason
from app.platform.tenancy import CompanyScoped


class ReadState(StrEnum):
    """Whether Tamga has read what a file says (ADR-0012). A document nobody asked to read (an
    inline logo) has none."""

    PENDING = "pending"
    READ = "read"
    UNREADABLE = "unreadable"


class Document(CompanyScoped, Base):
    """A file, stored once per company by content: the same CMR forwarded three times is one
    document, read once. Where it came from is recorded by whoever holds it (a message's
    attachments, an upload)."""

    __tablename__ = "documents"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "sha256"),
        # What waits for code to read it, oldest first.
        Index(
            "ix_documents_pending",
            "company_id",
            "created_at",
            postgresql_where=text("read_state = 'pending' AND NOT needs_vision"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32))
    storage_key: Mapped[str] = mapped_column(String(120))
    # The PDF the converter made of a Word, RTF or OpenDocument file (ADR-0012), shown and read in
    # its place: `<storage_key>.pdf`. Null until it is made.
    rendition_key: Mapped[str | None] = mapped_column(String(130))
    # The first name it arrived under; each attachment keeps its own name too.
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(200))
    size: Mapped[int]
    # A key in the document-type registry (documents/registry.py) once known: a CMR, an e-fatura.
    doc_type: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Reading it (documents/service.py, read_pending): asked for when it arrives as an attachment
    # or an upload, then set by the job that reads it.
    read_state: Mapped[ReadState | None] = mapped_column(
        text_enum(ReadState, "documents_read_state_check")
    )
    unreadable_reason: Mapped[Reason | None] = mapped_column(
        text_enum(Reason, "documents_unreadable_reason_check")
    )
    # What a person sees in it, read by code: a PDF's text layer (pages split by form feeds), a
    # spreadsheet's visible cells. Facts are read from it; empty for a photo or a scan.
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Pieces with content that its viewer hides and the reading left out (ADR-0013): a signal.
    hidden_parts: Mapped[int] = mapped_column(default=0, server_default="0")
    # A PDF's pages, a workbook's sheets; null before it is read, or for a picture.
    parts: Mapped[int | None]
    # A photo or a scanned page: only a vision model can read it, so code leaves it pending.
    needs_vision: Mapped[bool] = mapped_column(default=False, server_default=false())
    # What was read stops short of the file: pages past the hundredth, sheets, rows or columns past
    # what a workbook is read for, text past a million characters. What is missing may matter.
    truncated: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Times reading it failed for a reason outside the file (storage, the database), and when it
    # last did; after three, it is tried once an hour, so an outage costs a delay, not the file.
    read_failures: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    read_failed_at: Mapped[datetime | None]
    read_at: Mapped[datetime | None]


class Upload(CompanyScoped, Base):
    """A file someone put into Tamga by hand: the upload page, later the phone's share target.
    Uploading the same file twice is one document with two uploads."""

    __tablename__ = "uploads"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(300))
    # What the uploader typed with it; data, never instructions (root AGENTS.md, invariant 3).
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DocumentPart(CompanyScoped, Base):
    """A file taken out of an archive or a winmail.dat (ADR-0012): the member, stored once per
    company by content like any file, under the name it had in its container, as a mail's
    attachments are. A member that could not be taken out (a password) has no document, and says
    why."""

    __tablename__ = "document_parts"
    __table_args__ = (
        UniqueConstraint("container_id", "position"),
        CheckConstraint(
            "(document_id IS NULL) = (unreadable_reason IS NOT NULL)",
            name="document_parts_document_or_reason_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    container_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Its order in the container, and its name there: a path to show, never one on a disk.
    position: Mapped[int]
    name: Mapped[str] = mapped_column(String(1000))
    size: Mapped[int]
    unreadable_reason: Mapped[Reason | None] = mapped_column(
        text_enum(Reason, "document_parts_unreadable_reason_check")
    )
