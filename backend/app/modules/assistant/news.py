"""What is new on a project for Tamga to tell the owner, beyond what was linked to it: sources a
pack registers (the logistics pack's truck positions), so the core never imports a pack."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class News:
    """One thing new on a project."""

    # Told once, by this id (``agents.NEWS``).
    id: uuid.UUID
    project_id: uuid.UUID
    at: datetime
    # What the card shows it as, and the record a tap on it opens.
    kind: Literal["message", "document", "invoice", "truck"]
    opens: uuid.UUID
    # Code's words for it, given to the run as data.
    line: str
    # Items with the same topic are told as one line, the latest: the mails of one
    # conversation, the reports of one truck.
    topic: str = ""
    # When what it tells was true, if not when it came: a position reported late is not the
    # truck's latest.
    true_at: datetime | None = None

    @property
    def latest(self) -> datetime:
        return self.true_at or self.at


# What came onto the company's projects since then: (db, company, since) → news.
type Source = Callable[[AsyncSession, uuid.UUID, datetime], Awaitable[list[News]]]

_sources: list[Source] = []


def register(source: Source) -> None:
    _sources.append(source)


def sources() -> tuple[Source, ...]:
    return tuple(_sources)
