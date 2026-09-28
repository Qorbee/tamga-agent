import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base
from app.platform.tenancy import CompanyScoped


class Document(CompanyScoped, Base):
    """A file, stored once per company by content: the same CMR forwarded three times is one
    document, read once. Where it came from is recorded by whoever holds it (a message's
    attachments, an upload)."""

    __tablename__ = "documents"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("company_id", "sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32))
    storage_key: Mapped[str] = mapped_column(String(120))
    # The first name it arrived under; each attachment keeps its own name too.
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(200))
    size: Mapped[int]
    # A key in the document-type registry (documents/registry.py) once known: a CMR, an e-fatura.
    doc_type: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


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
