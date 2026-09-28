import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, or_, select
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
) -> None:
    await db.execute(
        insert(MessageAttachment)
        .values(
            company_id=company_id,
            message_id=message_id,
            document_id=document_id,
            filename=filename[:300],
        )
        .on_conflict_do_nothing(
            index_elements=[MessageAttachment.message_id, MessageAttachment.document_id]
        )
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
    page: PageParams,
) -> tuple[Sequence[tuple[Message, int]], int]:
    """Newest first, each with how many files it carried."""
    filters: list[ColumnElement[bool]] = [Message.company_id == company_id]
    if direction is not None:
        filters.append(Message.direction == direction)
    if channel is not None:
        filters.append(Message.channel == channel)
    if q:
        pattern = f"%{escape_like(q)}%"
        filters.append(
            or_(
                Message.subject.ilike(pattern, escape="\\"),
                Message.sender.ilike(pattern, escape="\\"),
                Message.body_text.ilike(pattern, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(Message).where(*filters))
    attachments = (
        select(func.count())
        .select_from(MessageAttachment)
        .where(MessageAttachment.message_id == Message.id)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(Message, attachments)
        .where(*filters)
        .order_by(Message.sent_at.desc(), Message.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return [(message, count) for message, count in rows.tuples()], total or 0


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
