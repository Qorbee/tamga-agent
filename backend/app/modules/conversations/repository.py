import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    Text,
    and_,
    cast,
    func,
    select,
    true,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversations.models import (
    Channel,
    Direction,
    MailFolder,
    Message,
    MessageAttachment,
    MessageLocation,
)
from app.platform import search
from app.platform.pagination import PageParams, escape_like


async def get_message_by_hash(
    db: AsyncSession, company_id: uuid.UUID, content_hash: bytes
) -> Message | None:
    message = await db.scalar(
        select(Message).where(
            Message.company_id == company_id, Message.content_hash == content_hash
        )
    )
    return message


async def insert_message_unless_stored(db: AsyncSession, message: Message) -> Message | None:
    """None when the company already holds a message with this content: the unique index decides."""
    values = {
        column.key: getattr(message, column.key)
        for column in Message.__table__.columns
        if getattr(message, column.key) is not None
    }
    inserted = await db.scalar(
        insert(Message)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Message.company_id, Message.content_hash])
        .returning(Message)
    )
    return inserted


async def add_location(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    message_id: uuid.UUID,
    mailbox: str,
    folder: str,
    uidvalidity: int,
    uid: int,
) -> None:
    await db.execute(
        insert(MessageLocation)
        .values(
            company_id=company_id,
            message_id=message_id,
            mailbox=mailbox,
            folder=folder,
            uidvalidity=uidvalidity,
            uid=uid,
        )
        .on_conflict_do_nothing(
            index_elements=[
                MessageLocation.company_id,
                MessageLocation.mailbox,
                MessageLocation.folder,
                MessageLocation.uidvalidity,
                MessageLocation.uid,
            ]
        )
    )


async def add_attachment(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    message_id: uuid.UUID,
    document_id: uuid.UUID,
    filename: str,
    inline: bool,
) -> None:
    statement = insert(MessageAttachment).values(
        company_id=company_id,
        message_id=message_id,
        document_id=document_id,
        filename=filename[:300],
        inline=inline,
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[MessageAttachment.message_id, MessageAttachment.document_id],
            # One file shown in the body and also attached beside it is an attachment.
            set_={"inline": MessageAttachment.inline & statement.excluded.inline},
        )
    )


async def set_inline(
    db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID, document_ids: set[uuid.UUID]
) -> None:
    """Marks which of a mail's files its body shows, and only those."""
    await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.message_id == message_id,
        )
        .values(inline=MessageAttachment.document_id.in_(document_ids))
    )


def _file_count() -> ScalarSelect[int]:
    """How many files a mail carried beside its text: the images its body shows are not counted."""
    return (
        select(func.count())
        .select_from(MessageAttachment)
        .where(
            MessageAttachment.company_id == Message.company_id,
            MessageAttachment.message_id == Message.id,
            MessageAttachment.inline.is_(False),
        )
        .scalar_subquery()
    )


async def get_folder(
    db: AsyncSession, company_id: uuid.UUID, mailbox: str, folder: str
) -> MailFolder | None:
    cursor = await db.scalar(
        select(MailFolder).where(
            MailFolder.company_id == company_id,
            MailFolder.mailbox == mailbox,
            MailFolder.folder == folder,
        )
    )
    return cursor


async def save_folder(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    mailbox: str,
    folder: str,
    uidvalidity: int,
    last_uid: int,
) -> None:
    statement = insert(MailFolder).values(
        company_id=company_id,
        mailbox=mailbox,
        folder=folder,
        uidvalidity=uidvalidity,
        last_uid=last_uid,
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[MailFolder.company_id, MailFolder.mailbox, MailFolder.folder],
            set_={
                "uidvalidity": statement.excluded.uidvalidity,
                "last_uid": statement.excluded.last_uid,
                "polled_at": statement.excluded.polled_at,
            },
        )
    )


async def thread_of(db: AsyncSession, company_id: uuid.UUID, rfc_message_id: str) -> str | None:
    thread: str | None = await db.scalar(
        select(Message.thread_key)
        .where(Message.company_id == company_id, Message.rfc_message_id == rfc_message_id)
        .limit(1)
    )
    return thread


async def move_thread(
    db: AsyncSession, company_id: uuid.UUID, *, from_key: str, to_key: str
) -> None:
    await db.execute(
        update(Message)
        .where(Message.company_id == company_id, Message.thread_key == from_key)
        .values(thread_key=to_key)
    )


async def mail_after(
    db: AsyncSession, company_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> Sequence[Message]:
    filters = [Message.company_id == company_id, Message.channel == Channel.EMAIL]
    if after is not None:
        filters.append(Message.id > after)
    rows = await db.scalars(select(Message).where(*filters).order_by(Message.id).limit(limit))
    return rows.all()


async def message_exists(db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(Message.id).where(Message.company_id == company_id, Message.id == message_id)
    )
    return found is not None


async def list_messages(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None,
    direction: Direction | None,
    channel: Channel | None,
    thread_key: str | None,
    page: PageParams,
) -> tuple[Sequence[tuple[Message, int]], int]:
    """Newest first, each with how many files it carried."""
    filters: list[ColumnElement[bool]] = [Message.company_id == company_id]
    if direction is not None:
        filters.append(Message.direction == direction)
    if channel is not None:
        filters.append(Message.channel == channel)
    if thread_key is not None:
        filters.append(Message.thread_key == thread_key)
    if q:
        filters.append(_matches(q))
    total = await db.scalar(select(func.count()).select_from(Message).where(*filters))
    rows = await db.execute(
        select(Message, _file_count())
        .where(*filters)
        .order_by(Message.sent_at.desc(), Message.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [(message, count) for message, count in rows.tuples()], total or 0


def _matches(q: str) -> ColumnElement[bool]:
    """Every term of the query found in the mail, both folded alike (`platform/search.py`); a
    query with no letters or digits matches every mail."""
    return and_(true(), *(_found(term) for term in search.terms(q)))


def _found(term: search.Term) -> ColumnElement[bool]:
    if term.anywhere:
        return Message.search_compact.like(f"%{escape_like(term.compact)}%", escape="\\")
    # The stored text puts a space before every word, so this matches from a word's start.
    return Message.search_text.like(f"% {escape_like(term.words)}%", escape="\\")


@dataclass(frozen=True, slots=True)
class ThreadRow:
    thread_key: str
    message_count: int
    first_sent_at: datetime
    last_sent_at: datetime


async def list_threads(
    db: AsyncSession, company_id: uuid.UUID, *, q: str | None, page: PageParams
) -> tuple[list[ThreadRow], int]:
    """Conversations, most recently active first. A mail stored twice (a copy in another mailbox)
    counts once — the same Message-ID and Date, as a sender that reuses a Message-ID gives two mails
    different Dates; a thread matches ``q`` when any of its mails does."""
    filters: list[ColumnElement[bool]] = [Message.company_id == company_id]
    if q:
        filters.append(
            Message.thread_key.in_(
                select(Message.thread_key).where(Message.company_id == company_id, _matches(q))
            )
        )
    one_mail = tuple_(
        func.coalesce(Message.rfc_message_id, cast(Message.id, Text)), Message.sent_at
    )
    grouped = (
        select(
            Message.thread_key,
            func.count(one_mail.distinct()).label("message_count"),
            func.min(Message.sent_at).label("first_sent_at"),
            func.max(Message.sent_at).label("last_sent_at"),
        )
        .where(*filters)
        .group_by(Message.thread_key)
    )
    total = await db.scalar(select(func.count(func.distinct(Message.thread_key))).where(*filters))
    rows = await db.execute(
        grouped.order_by(func.max(Message.sent_at).desc(), Message.thread_key)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [ThreadRow(*row) for row in rows.tuples()], total or 0


async def thread_messages(
    db: AsyncSession, company_id: uuid.UUID, thread_keys: Sequence[str]
) -> Sequence[tuple[Message, int]]:
    """Every mail of these threads, oldest first, each with how many files it carried."""
    rows = await db.execute(
        select(Message, _file_count())
        .where(Message.company_id == company_id, Message.thread_key.in_(thread_keys))
        .order_by(Message.sent_at, Message.id)
    )
    return rows.tuples().all()


async def get_message(
    db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    message: Message | None = await db.scalar(
        select(Message).where(Message.company_id == company_id, Message.id == message_id)
    )
    return message


async def attachments_of(
    db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID
) -> Sequence[MessageAttachment]:
    rows = await db.scalars(
        select(MessageAttachment)
        .where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.message_id == message_id,
        )
        .order_by(MessageAttachment.filename)
    )
    return rows.all()


def _waiting(tries: int, retry_before: datetime) -> ColumnElement[bool]:
    """Not processed yet, and not failed too often lately: one that failed ``tries`` times waits
    until its last failure is older than ``retry_before``."""
    return Message.processed_at.is_(None) & (
        (Message.process_failures < tries) | (Message.process_failed_at <= retry_before)
    )


async def companies_with_pending(
    db: AsyncSession, *, tries: int, retry_before: datetime
) -> list[uuid.UUID]:
    """The companies with mail waiting to be processed. The one read across companies, and it reads
    only their ids: each company's mail is then read under its own."""
    rows = await db.scalars(
        select(Message.company_id).where(_waiting(tries, retry_before)).distinct()
    )
    return list(rows)


async def pending_mail(
    db: AsyncSession, company_id: uuid.UUID, *, tries: int, retry_before: datetime, limit: int
) -> list[uuid.UUID]:
    """Oldest first, so a mail is read before the replies that answer it."""
    rows = await db.scalars(
        select(Message.id)
        .where(Message.company_id == company_id, _waiting(tries, retry_before))
        .order_by(Message.sent_at, Message.id)
        .limit(limit)
    )
    return list(rows)


async def add_process_failure(
    db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID, at: datetime
) -> None:
    await db.execute(
        update(Message)
        .where(Message.company_id == company_id, Message.id == message_id)
        .values(process_failures=Message.process_failures + 1, process_failed_at=at)
    )


async def processed_copies(
    db: AsyncSession, company_id: uuid.UUID, rfc_message_id: str, *, exclude: uuid.UUID
) -> Sequence[Message]:
    """Processed messages with this Message-ID that were read themselves, not as a copy."""
    rows = await db.scalars(
        select(Message)
        .where(
            Message.company_id == company_id,
            Message.rfc_message_id == rfc_message_id,
            Message.id != exclude,
            Message.processed_at.is_not(None),
            Message.copy_of_id.is_(None),
        )
        .order_by(Message.processed_at, Message.id)
    )
    return rows.all()
