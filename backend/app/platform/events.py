"""The company event stream: a short note that something changed, pushed to the browsers of the
company it changed in (ADR-0006, point 5).

A write publishes inside its own transaction: ``NOTIFY`` is delivered when that transaction
commits, never for one rolled back, and the same note twice in one transaction is delivered once.
Each API process holds one connection that ``LISTEN``s, opened by the first stream, and hands
each note to the streams of its company. A note names what changed, never its content: the
browser reads it again through the resources it already uses, as the user it is, so the stream
tells nobody anything a resource would not.

A stream keeps one note per kind and record until its browser takes them, so a burst (the
linker placing fifty conversations) reaches a browser as one note, and a slow browser holds a
handful, not a queue.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Final

import asyncpg
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.platform.db import open_session

logger = logging.getLogger(__name__)

CHANNEL: Final = "company_events"

# How long the listening connection goes unchecked. A connection that died without a word is
# only found by using it, so the next stream to open after this long asks it first.
_CHECK_AFTER: Final = 60.0
_CHECK_WITHIN: Final = 5.0
# A stream woken by a note waits this long for the rest of its burst before it is read.
_GATHER: Final = 2.0


class EventKind(StrEnum):
    CHAT = "chat"  # a chat got a message, or a run in it ended
    PROJECTS = "projects"  # projects and what they hold: placements, links, trucks, checklists
    DOCUMENTS = "documents"  # a file was read or typed
    THREADS = "threads"  # mail came in or was sorted
    INVOICES = "invoices"  # the accounting records were read
    DECISIONS = "decisions"  # a payable was evaluated, answered or paid
    MONEY = "money"  # accounts or movements were read, written or categorised
    CLOCK = "clock"  # a sandbox's day moved: every page counts from it


class CompanyEvent(BaseModel):
    kind: EventKind
    # The record the note is about, when it is about one (a chat).
    id: uuid.UUID | None = None
    # Tamga wrote it on its own, with nobody asking: the app tells whoever is looking.
    by_tamga: bool = False


async def publish(
    db: AsyncSession,
    company_id: uuid.UUID,
    kind: EventKind,
    record_id: uuid.UUID | None = None,
    *,
    by_tamga: bool = False,
) -> None:
    """Tells the company's browsers, once the caller's transaction commits, that ``kind``
    changed."""
    event = CompanyEvent(kind=kind, id=record_id, by_tamga=by_tamga)
    note = {"company_id": str(company_id), **event.model_dump(mode="json")}
    await db.execute(
        text("SELECT pg_notify(:channel, :note)"), {"channel": CHANNEL, "note": json.dumps(note)}
    )


async def publish_now(
    company_id: uuid.UUID,
    kinds: list[EventKind],
    record_id: uuid.UUID | None = None,
    *,
    by_tamga: bool = False,
) -> None:
    """``publish`` for writes already committed, in a transaction of its own."""
    async with open_session() as db:
        for kind in kinds:
            await publish(db, company_id, kind, record_id, by_tamga=by_tamga)
        await db.commit()


class Stream:
    """One browser's notes, kept until it takes them."""

    def __init__(self) -> None:
        self._waiting: dict[tuple[EventKind, uuid.UUID | None], CompanyEvent] = {}
        self._wake = asyncio.Event()
        self.closed = False

    def put(self, event: CompanyEvent) -> None:
        key = (event.kind, event.id)
        before = self._waiting.get(key)
        if before is not None and before.by_tamga:
            event = event.model_copy(update={"by_tamga": True})
        self._waiting[key] = event
        self._wake.set()

    def close(self) -> None:
        self.closed = True
        self._wake.set()

    async def take(self) -> list[CompanyEvent]:
        """What came since the last take, waiting for something; nothing once closed."""
        await self._wake.wait()
        if not self.closed:
            await asyncio.sleep(_GATHER)
        self._wake.clear()
        if self.closed:
            return []
        taken = list(self._waiting.values())
        self._waiting.clear()
        return taken


class _Hub:
    def __init__(self) -> None:
        self._streams: dict[uuid.UUID, set[Stream]] = {}
        self._connection: asyncpg.Connection | None = None
        self._checked = 0.0
        self._lock = asyncio.Lock()

    async def _listening(self) -> None:
        async with self._lock:
            connection = self._connection
            if connection is not None and not connection.is_closed():
                if time.monotonic() - self._checked < _CHECK_AFTER:
                    return
                try:
                    await asyncio.wait_for(connection.fetchval("SELECT 1"), _CHECK_WITHIN)
                    self._checked = time.monotonic()
                    return
                except OSError, TimeoutError, asyncpg.PostgresError:
                    logger.warning("the event stream's connection stopped answering; reopening it")
                    connection.terminate()
            connection = await asyncpg.connect(get_settings().database_url_libpq)
            await connection.add_listener(CHANNEL, self._heard)
            connection.add_termination_listener(self._lost)
            self._connection, self._checked = connection, time.monotonic()

    def _heard(self, _connection: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            note = json.loads(payload)
            company_id = uuid.UUID(note.pop("company_id"))
            event = CompanyEvent.model_validate(note)
        except ValueError, KeyError, TypeError, ValidationError:
            logger.warning("an event note that does not parse was dropped")
            return
        for stream in self._streams.get(company_id, ()):
            stream.put(event)

    def _lost(self, connection: Any) -> None:
        # Every stream ends, and each browser opens its stream again and reads what it shows:
        # a note published while nobody listened is not lost to it.
        if connection is self._connection:
            self._connection = None
        for streams in self._streams.values():
            for stream in streams:
                stream.close()

    @asynccontextmanager
    async def stream(self, company_id: uuid.UUID) -> AsyncIterator[Stream]:
        await self._listening()
        stream = Stream()
        self._streams.setdefault(company_id, set()).add(stream)
        try:
            yield stream
        finally:
            streams = self._streams.get(company_id, set())
            streams.discard(stream)
            if not streams:
                self._streams.pop(company_id, None)

    async def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()


_hub = _Hub()
stream = _hub.stream
close = _hub.close
