import uuid
from datetime import datetime

from sqlalchemy import LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base
from app.platform.tenancy import CompanyScoped


class Document(CompanyScoped, Base):
    """A file, stored once per company by content: the same CMR forwarded three times is one
    document, read once. Where it came from is recorded by whoever holds it (a message's
    attachments, an upload)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("company_id", "sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32))
    storage_key: Mapped[str] = mapped_column(String(120))
    # The first name it arrived under; each attachment keeps its own name too.
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(200))
    size: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
