import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    Text,
    and_,
    case,
    cast,
    delete,
    exists,
    func,
    or_,
    select,
    true,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.conversations.models import (
    Channel,
    Direction,
    MailFolder,
    MailModelRead,
    Message,
    MessageAttachment,
    MessageLocation,
    Thread,
    ThreadCategory,
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
    signature: bool = False,
) -> None:
    statement = insert(MessageAttachment).values(
        company_id=company_id,
        message_id=message_id,
        document_id=document_id,
        filename=filename[:300],
        inline=inline,
        signature=signature,
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[MessageAttachment.message_id, MessageAttachment.document_id],
            # One file shown in the body and also attached beside it is an attachment.
            set_={
                "inline": MessageAttachment.inline & statement.excluded.inline,
                "signature": MessageAttachment.signature & statement.excluded.signature,
            },
        )
    )


async def mark_signature(
    db: AsyncSession,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    signed: bool,
    message_id: uuid.UUID | None = None,
) -> None:
    """Marks a file as its senders' signature, or not, in every mail of the company that carried
    it, or in one."""
    await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.document_id == document_id,
            *([MessageAttachment.message_id == message_id] if message_id else []),
        )
        .values(signature=signed)
    )


async def set_inline(
    db: AsyncSession,
    company_id: uuid.UUID,
    message_id: uuid.UUID,
    document_ids: set[uuid.UUID],
    signed: set[uuid.UUID],
) -> None:
    """Marks which of a mail's files its body shows, and which sign its writer's mail."""
    await db.execute(
        update(MessageAttachment)
        .where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.message_id == message_id,
        )
        .values(
            inline=MessageAttachment.document_id.in_(document_ids),
            signature=MessageAttachment.document_id.in_(signed),
        )
    )


async def conversations_carrying(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """For each of these files, how many of the company's conversations have a mail that
    carries it."""
    if not document_ids:
        return {}
    rows = await db.execute(
        select(MessageAttachment.document_id, func.count(Message.thread_key.distinct()))
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(
            MessageAttachment.company_id == company_id,
            Message.company_id == company_id,
            MessageAttachment.document_id.in_(document_ids),
        )
        .group_by(MessageAttachment.document_id)
    )
    return {document_id: count for document_id, count in rows}


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
                # A renumbered folder is read from the start again, its older mail too.
                "history_since": case(
                    (
                        MailFolder.uidvalidity == statement.excluded.uidvalidity,
                        MailFolder.history_since,
                    ),
                    else_=None,
                ),
            },
        )
    )


async def stored_uids(
    db: AsyncSession,
    company_id: uuid.UUID,
    mailbox: str,
    folder: str,
    uidvalidity: int,
    uids: Sequence[int],
) -> set[int]:
    """Which of these UIDs of the folder are stored."""
    if not uids:
        return set()
    rows = await db.scalars(
        select(MessageLocation.uid).where(
            MessageLocation.company_id == company_id,
            MessageLocation.mailbox == mailbox,
            MessageLocation.folder == folder,
            MessageLocation.uidvalidity == uidvalidity,
            MessageLocation.uid.in_(uids),
        )
    )
    return set(rows)


async def save_history(
    db: AsyncSession, company_id: uuid.UUID, mailbox: str, folder: str, since: date
) -> None:
    await db.execute(
        update(MailFolder)
        .where(
            MailFolder.company_id == company_id,
            MailFolder.mailbox == mailbox,
            MailFolder.folder == folder,
        )
        .values(history_since=since)
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
    """Moves a thread's mail under another key, when it has any, and its sorting with it: the
    row takes the new key. When the thread it joins has a row of its own, that one is kept and
    the moved one goes; one that was noise for being all bulk mail is sorted again, now that it
    holds more."""
    moved = await db.execute(
        update(Message)
        .where(Message.company_id == company_id, Message.thread_key == from_key)
        .values(thread_key=to_key)
    )
    if not moved.rowcount:  # type: ignore[attr-defined]  # a DML statement's result has one
        return
    joined = select(Thread.id).where(Thread.company_id == company_id, Thread.thread_key == to_key)
    await db.execute(
        update(Thread)
        .where(Thread.company_id == company_id, Thread.thread_key == from_key, ~exists(joined))
        .values(thread_key=to_key)
    )
    await db.execute(
        delete(Thread).where(Thread.company_id == company_id, Thread.thread_key == from_key)
    )
    await db.execute(
        update(Thread)
        .where(
            Thread.company_id == company_id,
            Thread.thread_key == to_key,
            Thread.sorted_by == BY_HEADERS,
        )
        .values(category=None, sorted_by=None, sorted_at=None)
    )
    # The thread holds other mail than it did: whatever read it reads it again.
    await db.execute(
        update(Thread)
        .where(Thread.company_id == company_id, Thread.thread_key == to_key)
        .values(changed_at=func.now())
    )


async def mail_after(
    db: AsyncSession, company_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> Sequence[Message]:
    filters = [Message.company_id == company_id, Message.channel == Channel.EMAIL]
    if after is not None:
        filters.append(Message.id > after)
    rows = await db.scalars(select(Message).where(*filters).order_by(Message.id).limit(limit))
    return rows.all()


async def originals_after(
    db: AsyncSession, company_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> Sequence[Message]:
    """The messages read once, no copy of another, after ``after``, by id."""
    filters = [
        Message.company_id == company_id,
        Message.processed_at.is_not(None),
        Message.copy_of_id.is_(None),
    ]
    if after is not None:
        filters.append(Message.id > after)
    rows = await db.scalars(select(Message).where(*filters).order_by(Message.id).limit(limit))
    return rows.all()


async def attaches(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(MessageAttachment.id)
        .where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.document_id == document_id,
        )
        .limit(1)
    )
    return found is not None


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


async def carriers_of(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID, bool, bool, bool]]:
    """Every mail of the company that carried each of these files: the file, the mail, whether
    the mail's body showed it, whether the mail is bulk mail, and whether the file signs it."""
    if not document_ids:
        return []
    rows = await db.execute(
        select(
            MessageAttachment.document_id,
            MessageAttachment.message_id,
            MessageAttachment.inline,
            Message.bulk,
            MessageAttachment.signature,
        )
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(
            MessageAttachment.company_id == company_id,
            Message.company_id == company_id,
            MessageAttachment.document_id.in_(document_ids),
        )
        .order_by(MessageAttachment.document_id, MessageAttachment.message_id)
    )
    return list(rows.tuples().all())


async def messages_by_ids(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[tuple[Message, int]]:
    """These mails, each with how many files it carried."""
    rows = await db.execute(
        select(Message, _file_count()).where(Message.company_id == company_id, Message.id.in_(ids))
    )
    return rows.tuples().all()


async def mail_labels(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[tuple[uuid.UUID, str | None, str | None, str | None, datetime]]:
    rows = await db.execute(
        select(
            Message.id, Message.subject, Message.sender_name, Message.sender, Message.sent_at
        ).where(Message.company_id == company_id, Message.id.in_(ids))
    )
    return rows.tuples().all()


async def mail_senders(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[tuple[uuid.UUID, Direction, str | None, str | None]]:
    rows = await db.execute(
        select(Message.id, Message.direction, Message.sender, Message.subject).where(
            Message.company_id == company_id, Message.id.in_(ids)
        )
    )
    return rows.tuples().all()


async def first_carriers(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID, datetime, Channel]]:
    """For each of these files, the earliest mail that carried it: the file, the mail, its time
    and its channel."""
    rows = await db.execute(
        select(MessageAttachment.document_id, Message.id, Message.sent_at, Message.channel)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(
            MessageAttachment.company_id == company_id,
            Message.company_id == company_id,
            MessageAttachment.document_id.in_(document_ids),
        )
        .order_by(MessageAttachment.document_id, Message.sent_at, Message.id)
        .distinct(MessageAttachment.document_id)
    )
    return list(rows.tuples().all())


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
    return (await attachments_of_each(db, company_id, [message_id])).get(message_id, [])


async def attachments_of_each(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[MessageAttachment]]:
    """Each message's files by name, in one query."""
    rows = await db.scalars(
        select(MessageAttachment)
        .where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.message_id.in_(message_ids),
        )
        .order_by(MessageAttachment.filename)
    )
    files: dict[uuid.UUID, list[MessageAttachment]] = {}
    for row in rows:
        files.setdefault(row.message_id, []).append(row)
    return files


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


# Sorting conversations

BY_HEADERS = "headers"
BY_OWN_MAIL = "own_mail"
UNANSWERED = "unanswered"


def _in_thread(*conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    return exists().where(
        Message.company_id == Thread.company_id,
        Message.thread_key == Thread.thread_key,
        *conditions,
    )


async def note_mail(
    db: AsyncSession,
    company_id: uuid.UUID,
    thread_key: str,
    *,
    outbound: bool,
    bulk: bool,
    at: datetime,
) -> None:
    """Records that a mail of this thread was processed: the thread's row is made or marked as
    changed, and what the mail changes of its sorting is applied. A mail the company wrote makes
    the thread work, whatever it was. A mail that is not bulk, in a thread that was noise only
    for being all bulk, sends it back to be sorted."""
    row = insert(Thread).values(
        id=uuid.uuid7(), company_id=company_id, thread_key=thread_key, changed_at=at
    )
    await db.execute(
        row.on_conflict_do_update(
            index_elements=[Thread.company_id, Thread.thread_key], set_={"changed_at": at}
        )
    )
    this = (Thread.company_id == company_id, Thread.thread_key == thread_key)
    if outbound:
        await db.execute(
            update(Thread)
            .where(*this, Thread.category.is_distinct_from(ThreadCategory.WORK))
            .values(category=ThreadCategory.WORK, sorted_by=BY_OWN_MAIL, reason="", sorted_at=at)
        )
    elif not bulk:
        await db.execute(
            update(Thread)
            .where(*this, Thread.sorted_by == BY_HEADERS)
            .values(category=None, sorted_by=None, sorted_at=None)
        )


async def sort_by_rules(db: AsyncSession, company_id: uuid.UUID, at: datetime) -> int:
    """Sorts every waiting thread that needs no model: one the company wrote in is work, and one
    of bulk mail only is noise. Returns how many it sorted."""
    waiting = (Thread.company_id == company_id, Thread.category.is_(None))
    own = await db.execute(
        update(Thread)
        .where(*waiting, _in_thread(Message.direction == Direction.OUTBOUND))
        .values(category=ThreadCategory.WORK, sorted_by=BY_OWN_MAIL, sorted_at=at, changed_at=at)
    )
    bulk = await db.execute(
        update(Thread)
        .where(*waiting, _in_thread(), ~_in_thread(Message.bulk.is_(False)))
        .values(category=ThreadCategory.NOISE, sorted_by=BY_HEADERS, sorted_at=at, changed_at=at)
    )
    return (own.rowcount or 0) + (bulk.rowcount or 0)  # type: ignore[attr-defined]  # a DML result


def _unsorted(tries: int, retry_before: datetime) -> ColumnElement[bool]:
    return Thread.category.is_(None) & (
        (Thread.sort_failures < tries) | (Thread.sort_failed_at <= retry_before)
    )


async def companies_with_unsorted(
    db: AsyncSession, *, tries: int, retry_before: datetime
) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone."""
    rows = await db.scalars(
        select(Thread.company_id).where(_unsorted(tries, retry_before)).distinct()
    )
    return list(rows)


async def unsorted_threads(
    db: AsyncSession, company_id: uuid.UUID, *, tries: int, retry_before: datetime, limit: int
) -> list[str]:
    rows = await db.scalars(
        select(Thread.thread_key)
        .where(Thread.company_id == company_id, _unsorted(tries, retry_before))
        .order_by(Thread.created_at, Thread.id)
        .limit(limit)
    )
    return list(rows)


async def model_reads_since(
    db: AsyncSession, company_id: uuid.UUID, since: datetime, agent: str | None = None
) -> int:
    """Calls in which a model was sent this company's mail since then, whatever came of them;
    of one agent's, where ``agent`` names it."""
    query = (
        select(func.count())
        .select_from(MailModelRead)
        .where(MailModelRead.company_id == company_id, MailModelRead.read_at >= since)
    )
    if agent is not None:
        query = query.where(MailModelRead.agent == agent)
    return await db.scalar(query) or 0


async def add_model_read(db: AsyncSession, company_id: uuid.UUID, agent: str, at: datetime) -> None:
    db.add(MailModelRead(company_id=company_id, agent=agent, read_at=at))
    await db.flush()


async def set_sorting(
    db: AsyncSession,
    company_id: uuid.UUID,
    thread_key: str,
    *,
    category: ThreadCategory,
    sorted_by: str,
    reason: str,
    at: datetime,
) -> None:
    """Writes a thread's sorting, unless something sorted it while the model read."""
    await db.execute(
        update(Thread)
        .where(
            Thread.company_id == company_id,
            Thread.thread_key == thread_key,
            Thread.category.is_(None),
        )
        .values(
            category=category, sorted_by=sorted_by, reason=reason[:300], sorted_at=at, changed_at=at
        )
    )


async def add_sort_failure(
    db: AsyncSession, company_id: uuid.UUID, thread_key: str, at: datetime
) -> None:
    await db.execute(
        update(Thread)
        .where(Thread.company_id == company_id, Thread.thread_key == thread_key)
        .values(sort_failures=Thread.sort_failures + 1, sort_failed_at=at)
    )


async def get_thread(db: AsyncSession, company_id: uuid.UUID, thread_key: str) -> Thread | None:
    thread: Thread | None = await db.scalar(
        select(Thread).where(Thread.company_id == company_id, Thread.thread_key == thread_key)
    )
    return thread


async def first_mail(
    db: AsyncSession, company_id: uuid.UUID, thread_key: str, *, limit: int
) -> Sequence[Message]:
    """The thread's first mails as they were read, a copy in another mailbox left out."""
    rows = await db.scalars(
        select(Message)
        .where(
            Message.company_id == company_id,
            Message.thread_key == thread_key,
            Message.processed_at.is_not(None),
            Message.copy_of_id.is_(None),
        )
        .order_by(Message.sent_at, Message.id)
        .limit(limit)
    )
    return rows.all()


# What reads conversations next

# Sorted as these, a conversation is read further; noise is read by nothing.
READ_FURTHER = (ThreadCategory.WORK, ThreadCategory.AUTOMATED)


async def companies_with_read_threads(db: AsyncSession) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone."""
    rows = await db.scalars(
        select(Thread.company_id).where(Thread.category.in_(READ_FURTHER)).distinct()
    )
    return list(rows)


async def changed_threads(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    since: datetime,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[Thread]:
    """Threads sorted as work or automated that changed at or after ``since``, in the order they
    changed; ``after`` is the last one of the page before."""
    filters = [
        Thread.company_id == company_id,
        Thread.category.in_(READ_FURTHER),
        Thread.changed_at >= since,
    ]
    if after is not None:
        filters.append(tuple_(Thread.changed_at, Thread.id) > after)
    rows = await db.scalars(
        select(Thread).where(*filters).order_by(Thread.changed_at, Thread.id).limit(limit)
    )
    return rows.all()


async def get_thread_by_id(
    db: AsyncSession, company_id: uuid.UUID, thread_id: uuid.UUID
) -> Thread | None:
    thread: Thread | None = await db.scalar(
        select(Thread).where(Thread.company_id == company_id, Thread.id == thread_id)
    )
    return thread


async def find_threads(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> Sequence[Thread]:
    """Threads sorted as work or automated with a mail that matches ``q`` and was sent in the
    span, most recently active first."""
    matching = [Message.company_id == company_id, _matches(q)]
    if since is not None:
        matching.append(Message.sent_at >= since)
    if until is not None:
        matching.append(Message.sent_at < until)
    last_mail = (
        select(func.max(Message.sent_at))
        .where(Message.company_id == company_id, Message.thread_key == Thread.thread_key)
        .scalar_subquery()
    )
    rows = await db.scalars(
        select(Thread)
        .where(
            Thread.company_id == company_id,
            Thread.category.in_(READ_FURTHER),
            Thread.thread_key.in_(select(Message.thread_key).where(*matching)),
        )
        .order_by(last_mail.desc(), Thread.id)
        .limit(limit)
    )
    return rows.all()


async def threads_by_ids(
    db: AsyncSession, company_id: uuid.UUID, thread_ids: Sequence[uuid.UUID]
) -> Sequence[Thread]:
    rows = await db.scalars(
        select(Thread).where(Thread.company_id == company_id, Thread.id.in_(thread_ids))
    )
    return rows.all()


async def mail_as_read(
    db: AsyncSession, company_id: uuid.UUID, thread_keys: Sequence[str]
) -> Sequence[Message]:
    """These threads' mails as they were read, oldest first, a copy in another mailbox left
    out."""
    rows = await db.scalars(
        select(Message)
        .where(
            Message.company_id == company_id,
            Message.thread_key.in_(thread_keys),
            Message.processed_at.is_not(None),
            Message.copy_of_id.is_(None),
        )
        .order_by(Message.sent_at, Message.id)
    )
    return rows.all()


async def threads_of_messages(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID], *, limit: int
) -> Sequence[Thread]:
    """The threads that hold these messages, most recently changed first."""
    keys = select(Message.thread_key).where(
        Message.company_id == company_id, Message.id.in_(message_ids)
    )
    rows = await db.scalars(
        select(Thread)
        .where(Thread.company_id == company_id, Thread.thread_key.in_(keys))
        .order_by(Thread.changed_at.desc(), Thread.id)
        .limit(limit)
    )
    return rows.all()


async def thread_ids_of(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    rows = await db.execute(
        select(Message.id, Thread.id)
        .join(
            Thread,
            (Thread.company_id == Message.company_id) & (Thread.thread_key == Message.thread_key),
        )
        .where(Message.company_id == company_id, Message.id.in_(message_ids))
    )
    return {message_id: thread_id for message_id, thread_id in rows.all()}


async def messages_attaching(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> Sequence[MessageAttachment]:
    rows = await db.scalars(
        select(MessageAttachment).where(
            MessageAttachment.company_id == company_id,
            MessageAttachment.document_id.in_(document_ids),
        )
    )
    return rows.all()


async def touch_threads_carrying(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID, at: datetime
) -> None:
    """Marks as changed the threads with a mail that carries this file."""
    carriers = (
        select(Message.thread_key)
        .join(MessageAttachment, MessageAttachment.message_id == Message.id)
        .where(Message.company_id == company_id, MessageAttachment.document_id == document_id)
    )
    await db.execute(
        update(Thread)
        .where(Thread.company_id == company_id, Thread.thread_key.in_(carriers))
        .values(changed_at=at)
    )


async def mailboxes(db: AsyncSession, company_id: uuid.UUID) -> list[str]:
    rows = await db.scalars(
        select(MailFolder.mailbox).where(MailFolder.company_id == company_id).distinct()
    )
    return list(rows)


@dataclass(frozen=True, slots=True)
class DomainMail:
    domain: str
    sent: int
    received: int


def _sent_by_company() -> ColumnElement[bool]:
    """A mail the company sent: it, or a copy of it, is outbound. A copy kept from the Sent folder
    may be the only outbound one when another mailbox received the same mail first."""
    copy = aliased(Message)
    return or_(
        Message.direction == Direction.OUTBOUND,
        exists(
            select(copy.id).where(
                copy.company_id == Message.company_id,
                copy.copy_of_id == Message.id,
                copy.direction == Direction.OUTBOUND,
            )
        ),
    )


async def mail_by_sender_domain(
    db: AsyncSession, company_id: uuid.UUID, domains: Sequence[str] | None
) -> list[DomainMail]:
    """Distinct mails (a copy counts once) per sender domain as stored: sent by the company, and
    received. With no ``domains``, only those the company sent from."""
    domain = func.lower(func.split_part(Message.sender, "@", 2))
    outbound = _sent_by_company()
    sent = func.count().filter(outbound)
    received = func.count().filter(~outbound)
    statement = (
        select(domain, sent, received)
        .where(
            Message.company_id == company_id,
            Message.channel == Channel.EMAIL,
            Message.copy_of_id.is_(None),
            Message.sender.contains("@"),
        )
        .group_by(domain)
    )
    if domains is None:
        statement = statement.having(sent > 0)
    else:
        statement = statement.where(domain.in_(domains))
    rows = await db.execute(statement)
    return [DomainMail(domain, sent, received) for domain, sent, received in rows.tuples()]


async def outbound_among(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    rows = await db.scalars(
        select(Message.id).where(
            Message.company_id == company_id, Message.id.in_(ids), _sent_by_company()
        )
    )
    return set(rows)


async def own_senders(
    db: AsyncSession, company_id: uuid.UUID, since: datetime, limit: int
) -> Sequence[tuple[str, str, int]]:
    """Who wrote the company's own mail since then, by name and address, with how many mails,
    the busiest first."""
    count = func.count(Message.id)
    rows = await db.execute(
        select(Message.sender_name, Message.sender, count)
        .where(
            Message.company_id == company_id,
            Message.direction == Direction.OUTBOUND,
            Message.sent_at >= since,
            Message.sender_name.is_not(None),
            Message.sender.is_not(None),
        )
        .group_by(Message.sender_name, Message.sender)
        .order_by(count.desc())
        .limit(limit)
    )
    return [(name, sender, mails) for name, sender, mails in rows.tuples() if name and sender]
