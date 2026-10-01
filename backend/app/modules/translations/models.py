import uuid
from datetime import datetime

from sqlalchemy import LargeBinary, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.language import Language
from app.platform.tenancy import CompanyScoped


class Translation(CompanyScoped, Base):
    """What a text Tamga wrote reads as in another language. The text itself is never changed:
    this row is found by its hash, so a text written again is another text. A row is written
    before the model is asked, so what was asked counts against the day's budget whether or not
    an answer came."""

    __tablename__ = "translations"
    __table_args__ = (UniqueConstraint("company_id", "source_hash", "language"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # SHA-256 of the text as it was asked about.
    source_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    language: Mapped[Language] = mapped_column(text_enum(Language, "translations_language_check"))
    # Characters of the text asked about: what the day's budget counts.
    chars: Mapped[int]
    # Null: asked of the model and not answered, or not yet.
    text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
