"""Conversations: every item that arrives, stored before anything reads it (docs/architecture/06).

Mail arrives by polling the company's mailboxes over IMAP, read-only (ADR-0008). Each folder keeps
a cursor, because nothing on the server records what Tamga has read; each message is stored once,
by the hash of its raw bytes, with every folder it was seen in beside it.
"""

import asyncio
import hashlib
import imaplib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Mailbox, get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.conversations import html, repository
from app.modules.conversations.adapters.imap import FetchedMail, ListedFolder, MailboxReader
from app.modules.conversations.mail import UNREADABLE, ParsedMail, parse_mail
from app.modules.conversations.models import Channel, Direction, Message
from app.modules.conversations.schemas import (
    AttachmentOut,
    MessageOut,
    MessagePage,
    MessageSummary,
    ThreadPage,
    ThreadSummary,
)
from app.modules.documents import service as documents
from app.platform import search, storage
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import NotFound
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

SNIPPET_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class FolderPlace:
    mailbox: str
    folder: str
    uidvalidity: int
    sent: bool


async def poll_pilot_mailboxes() -> int:
    """Reads new mail from every configured mailbox into the pilot company. Returns how many
    messages were new. One mailbox failing (a changed password, a timeout) never stops the rest."""
    settings = get_settings()
    company_id = settings.pilot_company_id
    if company_id is None or not settings.mailboxes:
        return 0
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
    if company is None:
        # Fail the job where the queue shows it, rather than poll nothing and look healthy.
        raise RuntimeError("PILOT_COMPANY_ID names no company")
    # A replay is fed from stored history, never from live mail.
    if company.mode is CompanyMode.REPLAY:
        return 0

    own = {mailbox.username.lower() for mailbox in settings.mailboxes}
    stored = 0
    for mailbox in settings.mailboxes:
        try:
            stored += await poll_mailbox(mailbox, company_id=company_id, own_addresses=own)
        except (imaplib.IMAP4.error, OSError) as exc:
            # The server's answer and a masked address: never the password, never the full address.
            logger.warning("mail poll of %s failed: %s", _masked(mailbox), exc)
    return stored


def _masked(mailbox: Mailbox) -> str:
    local, at, domain = mailbox.username.partition("@")
    return f"{local[:2]}***{at}{domain} on {mailbox.host}"


async def poll_mailbox(mailbox: Mailbox, *, company_id: uuid.UUID, own_addresses: set[str]) -> int:
    reader = MailboxReader(mailbox)
    await asyncio.to_thread(reader.open)
    try:
        stored = 0
        for folder in await asyncio.to_thread(reader.folders):
            stored += await _poll_folder(reader, folder, company_id, own_addresses)
        return stored
    finally:
        await asyncio.to_thread(reader.close)


async def _poll_folder(
    reader: MailboxReader, listed: ListedFolder, company_id: uuid.UUID, own_addresses: set[str]
) -> int:
    settings = get_settings()
    username = reader.mailbox.username
    folder = listed.name
    uidvalidity, uidnext = await asyncio.to_thread(reader.examine, folder)
    async with open_session() as db:
        cursor = await repository.get_folder(db, company_id, username, folder)

    if cursor is not None and cursor.uidvalidity == uidvalidity:
        last_uid = cursor.last_uid
        uids = await asyncio.to_thread(reader.uids_after, last_uid)
    else:
        # A new folder, or one whose UIDs were renumbered: read the backfill window again. Mail
        # already stored only gains its new place.
        last_uid = 0
        since = (utcnow() - timedelta(days=settings.mail_backfill_days)).date()
        uids = await asyncio.to_thread(reader.uids_since, since)

    batch = [uid for uid in uids if uid > last_uid][: settings.mail_batch]
    if not batch:
        # Nothing to read: remember the folder as it is now, so the next poll asks only for newer
        # mail instead of the window again.
        if cursor is None or cursor.uidvalidity != uidvalidity:
            async with open_session() as db:
                await repository.save_folder(
                    db,
                    company_id=company_id,
                    mailbox=username,
                    folder=folder,
                    uidvalidity=uidvalidity,
                    last_uid=max(uidnext - 1, 0),
                )
                await db.commit()
        return 0

    place = FolderPlace(mailbox=username, folder=folder, uidvalidity=uidvalidity, sent=listed.sent)
    stored = 0
    for uid in batch:
        fetched = await asyncio.to_thread(reader.fetch, uid)
        # One transaction per message: the message, its place and the cursor commit together, so
        # a crash mid-folder resumes after the last message stored, never skips one.
        async with open_session() as db:
            new = await ingest_mail(
                db, company_id=company_id, place=place, fetched=fetched, own_addresses=own_addresses
            )
            await repository.save_folder(
                db,
                company_id=company_id,
                mailbox=username,
                folder=folder,
                uidvalidity=uidvalidity,
                last_uid=uid,
            )
            await db.commit()
        stored += int(new)
    return stored


async def ingest_mail(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    place: FolderPlace,
    fetched: FetchedMail,
    own_addresses: set[str],
) -> bool:
    """Stores one fetched mail and where it was seen. True when the message is new to the company.
    Runs in the caller's transaction and does not commit."""
    content_hash = hashlib.sha256(fetched.raw).digest()
    message = await repository.get_message_by_hash(db, company_id, content_hash)
    new = message is None
    if message is None:
        message = await _store_new_mail(
            db, company_id, content_hash, fetched, outbound_from=own_addresses, sent=place.sent
        )
    elif place.sent and message.direction is Direction.INBOUND:
        # First seen elsewhere (a copy the owner sent themselves); its Sent copy says who wrote it.
        message.direction = Direction.OUTBOUND
    await repository.add_location(
        db,
        company_id=company_id,
        message_id=message.id,
        mailbox=place.mailbox,
        folder=place.folder,
        uidvalidity=place.uidvalidity,
        uid=fetched.uid,
    )
    return new


async def _store_new_mail(
    db: AsyncSession,
    company_id: uuid.UUID,
    content_hash: bytes,
    fetched: FetchedMail,
    *,
    outbound_from: set[str],
    sent: bool,
) -> Message:
    try:
        parsed = parse_mail(fetched.raw)
    except Exception:
        # A mail the parser cannot read at all is kept raw with its fields empty, rather than left
        # unstored: its folder's cursor could never move past it, and every poll would fail on it.
        logger.exception("mail UID %d could not be parsed; stored raw", fetched.uid)
        parsed = UNREADABLE
    raw_key = storage.content_key(company_id, fetched.raw)
    await storage.put(raw_key, fetched.raw, content_type="message/rfc822")
    # Replies stored before the first mail with this Message-ID were keyed by it, for want of it.
    # Once any mail has the ID, a later one (a copy, or another mail reusing it) moves nothing.
    new_id = parsed.message_id
    if new_id is not None and await repository.thread_of(db, company_id, new_id) is not None:
        new_id = None
    candidate = Message(
        company_id=company_id,
        channel=Channel.EMAIL,
        direction=(
            Direction.OUTBOUND if sent or parsed.sender in outbound_from else Direction.INBOUND
        ),
        content_hash=content_hash,
        raw_key=raw_key,
        rfc_message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        thread_key=await _thread_key(db, company_id, parsed, content_hash),
        sender=parsed.sender,
        sender_name=parsed.sender_name,
        recipients=parsed.recipients,
        subject=parsed.subject,
        body_text=parsed.body_text,
        search_text=_search_text(parsed),
        sent_at=parsed.sent_at or fetched.internaldate or utcnow(),
    )
    message = await repository.insert_message_unless_stored(db, candidate)
    if message is None:  # stored a moment ago by another poll
        existing = await repository.get_message_by_hash(db, company_id, content_hash)
        if existing is None:
            raise RuntimeError("message vanished between insert and select")
        return existing
    if new_id is not None and new_id != message.thread_key:
        await repository.move_thread(db, company_id, from_key=new_id, to_key=message.thread_key)

    for attachment in parsed.attachments:
        document = await documents.store_file(
            db,
            company_id=company_id,
            data=attachment.data,
            filename=attachment.filename,
            content_type=attachment.content_type,
        )
        await repository.add_attachment(
            db,
            company_id=company_id,
            message_id=message.id,
            document_id=document.id,
            filename=attachment.filename,
            inline=attachment.inline,
        )
    return message


def _search_text(parsed: ParsedMail) -> str:
    """What a search finds the mail by. Files the body shows (a logo, `image001.png`) are left out:
    their names would match every mail from the sender."""
    return search.search_text(
        parsed.subject,
        parsed.sender_name,
        parsed.sender,
        *parsed.recipients,
        *(file.filename for file in parsed.attachments if not file.inline),
        parsed.body_text,
    )


async def _thread_key(
    db: AsyncSession, company_id: uuid.UUID, parsed: ParsedMail, content_hash: bytes
) -> str:
    """References start with the thread's first mail, which keys itself by its own Message-ID, so a
    reply and the mail it answers agree without either being stored first. A reply that names only
    its parent joins the parent's thread when the parent is here, and is moved there when the
    parent arrives later."""
    if parsed.references:
        return parsed.references[0]
    if parsed.in_reply_to:
        parent = await repository.thread_of(db, company_id, parsed.in_reply_to)
        return parent or parsed.in_reply_to
    return parsed.message_id or f"<{content_hash.hex()}>"


async def reread_stored_mail(
    db: AsyncSession, company_id: uuid.UUID, *, after: uuid.UUID | None, limit: int = 200
) -> uuid.UUID | None:
    """Reads one batch of stored originals again, to fill what the parser learned to keep after
    they were stored: the sender's name, which files the body shows inline, and the text a search
    looks in. Returns the last
    message read, to pass as ``after`` for the next batch; None when every mail has been read. The
    caller commits."""
    batch = await repository.mail_after(db, company_id, after=after, limit=limit)
    for message in batch:
        if message.raw_key is None:
            continue
        try:
            parsed = parse_mail(await storage.get(message.raw_key))
        except Exception:  # an unreadable original keeps what it has, as it did when stored
            logger.warning("stored mail %s could not be parsed again", message.id)
            continue
        message.sender_name = parsed.sender_name
        message.search_text = _search_text(parsed)
        await _mark_inline(db, company_id, message.id, parsed)
    return batch[-1].id if batch else None


async def _mark_inline(
    db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID, parsed: ParsedMail
) -> None:
    shown = {hashlib.sha256(file.data).digest() for file in parsed.attachments if file.inline}
    beside = {hashlib.sha256(file.data).digest() for file in parsed.attachments if not file.inline}
    attachments = await repository.attachments_of(db, company_id, message_id)
    files = await documents.get_documents(
        db, company_id, [attachment.document_id for attachment in attachments]
    )
    # One file shown in the body and also attached beside it is an attachment, as when stored.
    inline = {document_id for document_id, file in files.items() if file.sha256 in shown - beside}
    await repository.set_inline(db, company_id, message_id, inline)


# Reading what arrived


async def message_exists(db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID) -> bool:
    return await repository.message_exists(db, company_id, message_id)


async def list_messages(
    db: AsyncSession,
    actor: Actor,
    *,
    q: str | None,
    direction: Direction | None,
    channel: Channel | None,
    thread_key: str | None = None,
    page: PageParams,
) -> MessagePage:
    rows, total = await repository.list_messages(
        db,
        actor.company_id,
        q=q.strip() if q else None,
        direction=direction,
        channel=channel,
        thread_key=thread_key,
        page=page,
    )
    return MessagePage(
        items=[_summary(message, count) for message, count in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def list_threads(
    db: AsyncSession, actor: Actor, *, q: str | None, page: PageParams
) -> ThreadPage:
    rows, total = await repository.list_threads(
        db, actor.company_id, q=q.strip() if q else None, page=page
    )
    by_thread: dict[str, list[tuple[Message, int]]] = {}
    for message, count in await repository.thread_messages(
        db, actor.company_id, [row.thread_key for row in rows]
    ):
        by_thread.setdefault(message.thread_key, []).append((message, count))
    items = []
    for row in rows:
        mails = by_thread[row.thread_key]
        participants = dict.fromkeys(
            message.sender_name or message.sender for message, _ in mails if message.sender
        )
        latest, latest_count = mails[-1]
        items.append(
            ThreadSummary(
                thread_key=row.thread_key,
                subject=mails[0][0].subject,
                message_count=row.message_count,
                attachment_count=sum(count for _, count in _once(mails)),
                participants=[name for name in participants if name],
                first_sent_at=row.first_sent_at,
                last_sent_at=row.last_sent_at,
                latest=_summary(latest, latest_count),
            )
        )
    return ThreadPage(items=items, total=total, limit=page.limit, offset=page.offset)


def _once(mails: list[tuple[Message, int]]) -> list[tuple[Message, int]]:
    """Each mail once, however many mailboxes held a copy (the same Message-ID and Date)."""
    seen: set[tuple[str, datetime]] = set()
    kept = []
    for message, count in mails:
        key = (message.rfc_message_id or str(message.id), message.sent_at)
        if key not in seen:
            seen.add(key)
            kept.append((message, count))
    return kept


async def get_raw(db: AsyncSession, actor: Actor, message_id: uuid.UUID) -> bytes:
    """The mail exactly as the server gave it, for opening in a mail client."""
    message = await repository.get_message(db, actor.company_id, message_id)
    if message is None or message.raw_key is None:
        raise NotFound("No such message")
    return await storage.get(message.raw_key)


async def get_message(db: AsyncSession, actor: Actor, message_id: uuid.UUID) -> MessageOut:
    message = await repository.get_message(db, actor.company_id, message_id)
    if message is None:
        raise NotFound("No such message")
    attachments = await repository.attachments_of(db, actor.company_id, message_id)
    files = await documents.get_documents(
        db, actor.company_id, [attachment.document_id for attachment in attachments]
    )
    shown = await _html_of(message)
    stored = {file.sha256: document_id for document_id, file in files.items()}
    images = shown.images.items() if shown else []
    return MessageOut(
        **_summary(message, sum(not attachment.inline for attachment in attachments)).model_dump(),
        body_text=message.body_text,
        body_html=shown.html if shown else None,
        remote_images=shown.remote_images if shown else 0,
        inline_images={cid: stored[digest] for cid, digest in images if digest in stored},
        in_reply_to=message.in_reply_to,
        attachments=[
            AttachmentOut(
                document_id=attachment.document_id,
                filename=attachment.filename,
                content_type=files[attachment.document_id].content_type,
                size=files[attachment.document_id].size,
                preview=documents.preview_of(
                    files[attachment.document_id].content_type, attachment.filename
                ),
                inline=attachment.inline,
            )
            for attachment in attachments
        ],
    )


async def _html_of(message: Message) -> html.MailHtml | None:
    """The mail's HTML body, sanitized as it is read: a stricter sanitizer needs no backfill."""
    if message.raw_key is None:
        return None
    try:
        return await asyncio.to_thread(html.render, await storage.get(message.raw_key))
    except Exception:  # an unreachable original or a broken body; the stored text is still shown
        logger.exception("the HTML of mail %s could not be read", message.id)
        return None


def _summary(message: Message, attachment_count: int) -> MessageSummary:
    return MessageSummary(
        id=message.id,
        channel=message.channel,
        direction=message.direction,
        thread_key=message.thread_key,
        rfc_message_id=message.rfc_message_id,
        sender=message.sender,
        sender_name=message.sender_name,
        recipients=message.recipients,
        subject=message.subject,
        snippet=" ".join(message.body_text.split())[:SNIPPET_LENGTH],
        sent_at=message.sent_at,
        attachment_count=attachment_count,
    )
