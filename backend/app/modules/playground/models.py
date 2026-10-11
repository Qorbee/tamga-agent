import uuid
from datetime import date

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class World(Base):
    """A sandbox's scenario, and how far its script has played on the sandbox's clock."""

    __tablename__ = "playground_worlds"

    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    # A registered scenario's key; a scenario a later release drops leaves its worlds unscripted.
    scenario: Mapped[str] = mapped_column(String(40))
    # The sandbox's day when it opened: a script's step is due a number of days after it.
    opened_on: Mapped[date | None]
    # How many of the script's steps have happened, in order.
    played: Mapped[int] = mapped_column(default=0, server_default="0")
