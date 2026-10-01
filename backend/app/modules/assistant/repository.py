import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant.models import Chat, ChatMessage, ChatRead, ChatRecord, ChatRun, Origin
from app.platform.pagination import PageParams


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
    await db.execute(delete(Chat).where(Chat.company_id == chat.company_id, Chat.id == chat.id))


async def messages(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID
) -> list[dict[str, Any]]:
    """A chat's messages as they were stored, in the order they were written."""
    rows = await db.scalars(
        select(ChatMessage.message)
        .where(ChatMessage.company_id == company_id, ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.position)
    )
    return list(rows)


async def add_messages(
    db: AsyncSession,
    chat: Chat,
    stored: Sequence[dict[str, Any]],
    *,
    run_id: uuid.UUID,
    began_by: Origin,
    user_id: uuid.UUID | None,
) -> None:
    """Appends a run's messages after those the chat holds. The caller holds the chat's row
    locked, so two runs of one chat never take the same place."""
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
            run_id=run_id,
            began_by=began_by,
            user_id=user_id,
            message=message,
        )
        for offset, message in enumerate(stored, start=1)
    )
    await db.flush()


async def touch_record(
    db: AsyncSession, chat: Chat, record_kind: str, record_id: uuid.UUID, at: datetime
) -> None:
    """Notes that the chat holds this record, as of now."""
    row = insert(ChatRecord).values(
        id=uuid.uuid7(),
        company_id=chat.company_id,
        chat_id=chat.id,
        record_kind=record_kind,
        record_id=record_id,
        touched_at=at,
    )
    await db.execute(
        row.on_conflict_do_update(
            index_elements=[ChatRecord.chat_id, ChatRecord.record_kind, ChatRecord.record_id],
            set_={"touched_at": at},
        )
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


async def settle_run(
    db: AsyncSession, company_id: uuid.UUID, run_id: uuid.UUID, requests: int
) -> None:
    """Sets what a run was reserved to what it made."""
    await db.execute(
        update(ChatRun)
        .where(ChatRun.company_id == company_id, ChatRun.id == run_id)
        .values(requests=requests)
    )


async def requests_since(db: AsyncSession, company_id: uuid.UUID, since: datetime) -> int:
    """Requests the assistant made for this company since then, and those its runs in flight
    may still make: what the day's budget counts."""
    spent = await db.scalar(
        select(func.coalesce(func.sum(ChatRun.requests), 0)).where(
            ChatRun.company_id == company_id, ChatRun.ran_at > since
        )
    )
    return int(spent or 0)
