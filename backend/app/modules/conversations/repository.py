import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversations.models import (
    MailFolder,
    Message,
    MessageAttachment,
    MessageLocation,
)


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
