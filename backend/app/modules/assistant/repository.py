import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, text, true, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant.models import (
    Chat,
    ChatMessage,
    ChatRead,
    ChatRecord,
    ChatRun,
    MessageKind,
    Origin,
)
from app.platform import search
from app.platform.pagination import PageParams, escape_like


async def add(db: AsyncSession, row: Any) -> None:
    db.add(row)
    await db.flush()


async def get_chat(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID, *, for_update: bool = False
) -> Chat | None:
    statement = select(Chat).where(Chat.company_id == company_id, Chat.id == chat_id)
    if for_update:
        statement = statement.with_for_update()
    chat: Chat | None = await db.scalar(statement)
    return chat


def _unread(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Whether a chat holds a message newer than what this user read of it. A chat with no
    message has nothing to read."""
    read_at = (
        select(ChatRead.read_at)
        .where(ChatRead.chat_id == Chat.id, ChatRead.user_id == user_id)
        .scalar_subquery()
    )
    return and_(
        Chat.last_message_at.is_not(None), func.coalesce(read_at < Chat.last_message_at, True)
    )


async def list_chats(
    db: AsyncSession,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    archived: bool,
    page: PageParams,
) -> tuple[Sequence[tuple[Chat, bool]], int]:
    """A company's chats with whether this user has read each: the unread first, then the most
    recently written in."""
    filters = [Chat.company_id == company_id, Chat.archived_at.is_not(None) == archived]
    total = await db.scalar(select(func.count()).select_from(Chat).where(*filters))
    unread = _unread(user_id)
    rows = await db.execute(
        select(Chat, unread)
        .where(*filters)
        .order_by(
            unread.desc(),
            func.coalesce(Chat.last_message_at, Chat.created_at).desc(),
            Chat.id.desc(),
        )
        .limit(page.limit)
        .offset(page.offset)
    )
    return [(chat, bool(is_unread)) for chat, is_unread in rows.all()], total or 0


async def is_unread(db: AsyncSession, chat: Chat, user_id: uuid.UUID) -> bool:
    return bool(await db.scalar(select(_unread(user_id)).where(Chat.id == chat.id)))


async def mark_read(db: AsyncSession, chat: Chat, user_id: uuid.UUID, at: datetime) -> None:
    row = insert(ChatRead).values(
        id=uuid.uuid7(), company_id=chat.company_id, chat_id=chat.id, user_id=user_id, read_at=at
    )
    await db.execute(
        row.on_conflict_do_update(
            index_elements=[ChatRead.chat_id, ChatRead.user_id], set_={"read_at": at}
        )
    )


async def delete_chat(db: AsyncSession, chat: Chat) -> None:
    """The chat and all it holds. Its runs stay, as the budget counts them, but lose why they
    began: in the chat's words, it goes with the chat."""
    await db.execute(
        update(ChatRun)
        .where(
            ChatRun.company_id == chat.company_id,
            ChatRun.id.in_(
                select(ChatMessage.run_id).where(
                    ChatMessage.company_id == chat.company_id, ChatMessage.chat_id == chat.id
                )
            ),
        )
        .values(reason=None)
    )
    await db.execute(delete(Chat).where(Chat.company_id == chat.company_id, Chat.id == chat.id))


async def messages(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID, *, after: int | None = None
) -> list[dict[str, Any]]:
    """A chat's messages of the model layer as they were stored, in the order they were
    written; ``after``, only those past that place."""
    filters = [
        ChatMessage.company_id == company_id,
        ChatMessage.chat_id == chat_id,
        ChatMessage.kind == MessageKind.MODEL,
    ]
    if after is not None:
        filters.append(ChatMessage.position > after)
    rows = await db.scalars(
        select(ChatMessage.message).where(*filters).order_by(ChatMessage.position)
    )
    return list(rows)


async def notes(db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID) -> ChatMessage | None:
    """The notes of the chat's last compaction, when it was ever compacted."""
    row: ChatMessage | None = await db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.company_id == company_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.kind == MessageKind.NOTES,
        )
        .order_by(ChatMessage.position.desc())
        .limit(1)
    )
    return row


async def rows(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID, *, after: int | None = None
) -> Sequence[ChatMessage]:
    """Everything a chat holds, messages, cards and notes, in order: what a reader is shown;
    ``after``, only what is past that place."""
    filters = [ChatMessage.company_id == company_id, ChatMessage.chat_id == chat_id]
    if after is not None:
        filters.append(ChatMessage.position > after)
    found = await db.scalars(select(ChatMessage).where(*filters).order_by(ChatMessage.position))
    return found.all()


async def add_messages(
    db: AsyncSession,
    chat: Chat,
    stored: Sequence[dict[str, Any]],
    *,
    run_id: uuid.UUID,
    began_by: Origin,
    user_id: uuid.UUID | None,
    kind: MessageKind = MessageKind.MODEL,
) -> None:
    """Appends a run's messages, or the cards shown with them, after what the chat holds. The
    caller holds the chat's row locked, so two runs of one chat never take the same place."""
    last = await db.scalar(
        select(func.max(ChatMessage.position)).where(
            ChatMessage.company_id == chat.company_id, ChatMessage.chat_id == chat.id
        )
    )
    start = -1 if last is None else last
    db.add_all(
        ChatMessage(
            company_id=chat.company_id,
            chat_id=chat.id,
            position=start + offset,
            kind=kind,
            run_id=run_id,
            began_by=began_by,
            user_id=user_id,
            message=message,
        )
        for offset, message in enumerate(stored, start=1)
    )
    await db.flush()


async def touch_record(
    db: AsyncSession,
    chat: Chat,
    record_kind: str,
    record_id: uuid.UUID,
    at: datetime,
    *,
    raised: bool = False,
) -> None:
    """Notes that the chat holds this record, as of now; ``raised``, that Tamga wrote about it
    here on its own."""
    row = insert(ChatRecord).values(
        id=uuid.uuid7(),
        company_id=chat.company_id,
        chat_id=chat.id,
        record_kind=record_kind,
        record_id=record_id,
        touched_at=at,
        raised_at=at if raised else None,
    )
    changed: dict[str, Any] = {"touched_at": at}
    if raised:
        changed["raised_at"] = at
    await db.execute(
        row.on_conflict_do_update(
            index_elements=[ChatRecord.chat_id, ChatRecord.record_kind, ChatRecord.record_id],
            set_=changed,
        )
    )


async def move_record(
    db: AsyncSession,
    company_id: uuid.UUID,
    record_kind: str,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> None:
    """Every chat that held the one record holds the other in its place. A chat that held both
    holds it once, as lately touched and as lately raised as either was."""
    rows = (
        await db.scalars(
            select(ChatRecord).where(
                ChatRecord.company_id == company_id,
                ChatRecord.record_kind == record_kind,
                ChatRecord.record_id.in_([source_id, target_id]),
            )
        )
    ).all()
    there = {row.chat_id: row for row in rows if row.record_id == target_id}
    for row in rows:
        if row.record_id != source_id:
            continue
        other = there.get(row.chat_id)
        if other is None:
            row.record_id = target_id
            continue
        other.touched_at = max(other.touched_at, row.touched_at)
        other.raised_at = max(
            (at for at in (other.raised_at, row.raised_at) if at is not None), default=None
        )
        await db.delete(row)
    await db.flush()


async def cards_showing(
    db: AsyncSession, company_id: uuid.UUID, card_kind: str, record_id: uuid.UUID
) -> Sequence[ChatMessage]:
    """The cards of one kind, in any chat, that show this record."""
    found = await db.scalars(
        select(ChatMessage).where(
            ChatMessage.company_id == company_id,
            ChatMessage.kind == MessageKind.CARD,
            ChatMessage.message.contains({"kind": card_kind, "id": str(record_id)}),
        )
    )
    return found.all()


async def raised(
    db: AsyncSession, company_id: uuid.UUID, record_kind: str, record_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Those of the records that Tamga already wrote about, in any chat."""
    found = await db.scalars(
        select(ChatRecord.record_id).where(
            ChatRecord.company_id == company_id,
            ChatRecord.record_kind == record_kind,
            ChatRecord.record_id.in_(record_ids),
            ChatRecord.raised_at.is_not(None),
        )
    )
    return set(found)


async def holding(
    db: AsyncSession, company_id: uuid.UUID, records: Sequence[tuple[str, uuid.UUID]]
) -> Chat | None:
    """The chat that most recently held one of the records and is not archived (ADR-0018,
    point 4): where a finding about them has its context. Locked for the caller."""
    chat: Chat | None = await db.scalar(
        select(Chat)
        .join(
            ChatRecord,
            and_(ChatRecord.company_id == Chat.company_id, ChatRecord.chat_id == Chat.id),
        )
        .where(
            Chat.company_id == company_id,
            Chat.archived_at.is_(None),
            tuple_(ChatRecord.record_kind, ChatRecord.record_id).in_(records),
        )
        .order_by(ChatRecord.touched_at.desc(), Chat.id.desc())
        .limit(1)
        .with_for_update(of=Chat)
    )
    return chat


async def digest_of(db: AsyncSession, company_id: uuid.UUID, day: date) -> Chat | None:
    """The company's digest of that day, locked for the caller."""
    chat: Chat | None = await db.scalar(
        select(Chat).where(Chat.company_id == company_id, Chat.digest_on == day).with_for_update()
    )
    return chat


async def opened_since(
    db: AsyncSession, company_id: uuid.UUID, origin: Origin, since: datetime
) -> int:
    """Chats of this origin opened for the company since then, its digests aside: those are
    what the day's chats overflow into."""
    count = await db.scalar(
        select(func.count())
        .select_from(Chat)
        .where(
            Chat.company_id == company_id,
            Chat.origin == origin,
            Chat.created_at > since,
            Chat.digest_on.is_(None),
        )
    )
    return count or 0


async def drop_abandoned(
    db: AsyncSession, company_id: uuid.UUID, origin: Origin, before: datetime
) -> None:
    """Removes the chats of this origin that hold no message and have no run since ``before``:
    opened for a run whose process then stopped, they would stay empty for ever."""
    await db.execute(
        delete(Chat).where(
            Chat.company_id == company_id,
            Chat.origin == origin,
            Chat.last_message_at.is_(None),
            Chat.running_since.is_(None) | (Chat.running_since < before),
        )
    )


async def end_run(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID, began: datetime
) -> None:
    """The chat's run that began then is over: the chat is free for its next. A run that began
    later (this one was given up for dead) keeps the chat."""
    await db.execute(
        update(Chat)
        .where(Chat.company_id == company_id, Chat.id == chat_id, Chat.running_since == began)
        .values(running_since=None)
    )


async def records(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID
) -> Sequence[ChatRecord]:
    rows = await db.scalars(
        select(ChatRecord)
        .where(ChatRecord.company_id == company_id, ChatRecord.chat_id == chat_id)
        .order_by(ChatRecord.touched_at, ChatRecord.id)
    )
    return rows.all()


async def lock_budget(db: AsyncSession, company_id: uuid.UUID) -> None:
    """One request at a time reads the company's chat budget and reserves from it, until it
    commits."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"tamga.chat_budget.{company_id}"},
    )


async def add_run(
    db: AsyncSession,
    company_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    began_by: Origin,
    requests: int,
    at: datetime,
) -> None:
    await add(
        db,
        ChatRun(id=run_id, company_id=company_id, began_by=began_by, requests=requests, ran_at=at),
    )


async def give_reason(
    db: AsyncSession, company_id: uuid.UUID, run_id: uuid.UUID, reason: str
) -> None:
    """Why Tamga took a turn on its own, kept once the turn's messages are: a run that stored
    nothing keeps no words of a chat."""
    await db.execute(
        update(ChatRun)
        .where(ChatRun.company_id == company_id, ChatRun.id == run_id)
        .values(reason=reason)
    )


async def run_reasons(
    db: AsyncSession, company_id: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str | None, datetime]]:
    """Why and when each of these runs Tamga took on its own began, by its id; a run with no
    reason (a compaction before a turn) is none of them."""
    if not run_ids:
        return {}
    found = await db.execute(
        select(ChatRun.id, ChatRun.reason, ChatRun.ran_at).where(
            ChatRun.company_id == company_id,
            ChatRun.id.in_(run_ids),
            ChatRun.began_by == Origin.TAMGA,
            ChatRun.reason.is_not(None),
        )
    )
    return {run_id: (reason, at) for run_id, reason, at in found.all()}


async def settle_run(
    db: AsyncSession, company_id: uuid.UUID, run_id: uuid.UUID, requests: int
) -> None:
    """Sets what a run was reserved to what it made."""
    await db.execute(
        update(ChatRun)
        .where(ChatRun.company_id == company_id, ChatRun.id == run_id)
        .values(requests=requests)
    )


async def requests_since(
    db: AsyncSession, company_id: uuid.UUID, began_by: Origin, since: datetime
) -> int:
    """Requests the assistant made for this company since then in the runs the owner began, or
    in those Tamga began, and those such runs in flight may still make: what each day's budget
    counts."""
    spent = await db.scalar(
        select(func.coalesce(func.sum(ChatRun.requests), 0)).where(
            ChatRun.company_id == company_id,
            ChatRun.began_by == began_by,
            ChatRun.ran_at > since,
        )
    )
    return int(spent or 0)


async def quiet_chats(db: AsyncSession, before: datetime, limit: int) -> Sequence[Chat]:
    """Chats of any company quiet since before then, that a person asked in, with no run in
    flight, whose summary is missing or older than their last message: the oldest first."""
    asked = (
        select(ChatMessage.id)
        .where(
            ChatMessage.company_id == Chat.company_id,
            ChatMessage.chat_id == Chat.id,
            ChatMessage.began_by == Origin.OWNER,
        )
        .exists()
    )
    found = await db.scalars(
        select(Chat)
        .where(
            Chat.last_message_at < before,
            asked,
            Chat.running_since.is_(None),
            Chat.digest_on.is_(None),
            or_(Chat.summarized_at.is_(None), Chat.summarized_at < Chat.last_message_at),
        )
        .order_by(Chat.last_message_at)
        .limit(limit)
    )
    return found.all()


async def find_summaries(
    db: AsyncSession, company_id: uuid.UUID, q: str, *, leaving_out: uuid.UUID, limit: int
) -> Sequence[Chat]:
    """The company's summarized chats whose title or summary holds every term of the query,
    the most recent first; ``leaving_out``, the chat that searches."""
    terms = [_found(term) for term in search.terms(q)]
    found = await db.scalars(
        select(Chat)
        .where(
            Chat.company_id == company_id,
            Chat.id != leaving_out,
            Chat.summary != "",
            and_(true(), *terms),
        )
        .order_by(Chat.last_message_at.desc())
        .limit(limit)
    )
    return found.all()


def _found(term: search.Term) -> ColumnElement[bool]:
    if term.anywhere:
        compact = func.replace(Chat.search_text, " ", "")
        return compact.like(f"%{escape_like(term.compact)}%", escape="\\")
    return Chat.search_text.like(f"% {escape_like(term.words)}%", escape="\\")
