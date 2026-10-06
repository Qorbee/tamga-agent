"""Conversations: every item that arrives, stored before anything reads it (docs/architecture/06).

Mail arrives by polling the company's mailboxes over IMAP, read-only (ADR-0008). Each folder keeps
a cursor, because nothing on the server records what Tamga has read; each message is stored once,
by the hash of its raw bytes, with every folder it was seen in beside it.

A stored message waits to be processed: a job reads it once, oldest first, keeping the text its
writer typed and the facts code finds in it. A copy of a mail stored from a second mailbox is read
as the first one was, not again.

A conversation is then sorted (work, automated or noise), by code where its mail says enough and
by the ``intake`` agent otherwise, so that what reads conversations next reads only work.
"""

import asyncio
import hashlib
import imaplib
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from pydantic_ai import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Mailbox, get_settings
from app.modules.companies import service as companies
from app.modules.companies.models import CompanyMode
from app.modules.conversations import agents, html, quoted, repository
from app.modules.conversations.adapters.imap import FetchedMail, ListedFolder, MailboxReader
from app.modules.conversations.mail import UNREADABLE, ParsedMail, parse_mail
from app.modules.conversations.models import Channel, Direction, Message, Thread, ThreadCategory
from app.modules.conversations.repository import DomainMail as DomainMail
from app.modules.conversations.schemas import (
    AttachmentOut,
    MessageOut,
    MessagePage,
    MessageSummary,
    ThreadPage,
    ThreadSummary,
)
from app.modules.documents import service as documents
from app.modules.documents.schemas import Holder
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.platform import events, llm, maildomains, search, storage
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import NotFound
from app.platform.html import SafeHtml, sanitize
from app.platform.items import ItemKind
from app.platform.language import Language
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

SNIPPET_LENGTH: Final = 200
# Messages one company has processed per run; the next run, a minute later, takes the rest.
PROCESS_BATCH: Final = 100
# How many times a message is tried each run before it waits an hour between tries (a bug, an
# outage, or a mail nobody can read).
PROCESS_TRIES: Final = 3
PROCESS_RETRY_AFTER: Final = timedelta(hours=1)
# Older mail is read a month at a time, newest month first, and a poll looks at no more than a
# year of months in one folder, so a folder with years of nothing to read costs a few polls.
HISTORY_STEP: Final = timedelta(days=30)
HISTORY_MONTHS_A_POLL: Final = 12
# Conversations one company has sorted by the model per run, a call each.
SORT_BATCH: Final = 30
SORT_TRIES: Final = 3
SORT_RETRY_AFTER: Final = timedelta(hours=1)
# The budget of model reads (MAIL_MODEL_READS_PER_DAY) counts those made in this long.
SORT_WINDOW: Final = timedelta(days=1)
# What the model is shown of a conversation: its first mails, and this much of each one's text.
_SORT_MAILS: Final = 3
_SORT_TEXT: Final = 1500
# One answer, and one more request when it fails the output's checks.
_SORT_LIMITS: Final = UsageLimits(request_limit=2)
# A picture this many of a company's conversations show in their text is a sender's furniture (a
# signature's banner), not part of any one of them.
_FURNITURE_CONVERSATIONS: Final = 3
# A file more conversations than this carry is a sender's standing paper (terms, a rate sheet),
# no one job's. A job's own paper is sent on to a few: the client, the carrier, the accountant.
_STANDING_CONVERSATIONS: Final = 3
# A file more conversations carry than this says nothing new of any one of them when it is read:
# the terms sent with every offer, a logo attached to every mail.
_COMMON_CONVERSATIONS: Final = 10


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
    """Reads a folder's new mail, oldest first, then, with what is left of the poll's batch, mail
    older than it has read (``_read_history``)."""
    settings = get_settings()
    username = reader.mailbox.username
    folder = listed.name
    uidvalidity, uidnext = await asyncio.to_thread(reader.examine, folder)
    async with open_session() as db:
        cursor = await repository.get_folder(db, company_id, username, folder)
    since = (utcnow() - timedelta(days=settings.mail_backfill_days)).date()
    known = cursor is not None and cursor.uidvalidity == uidvalidity

    if cursor is not None and known:
        last_uid = cursor.last_uid
        uids = await asyncio.to_thread(reader.uids_after, last_uid)
    else:
        # A new folder, or one whose UIDs were renumbered: read the backfill window again. Mail
        # already stored only gains its new place.
        last_uid = 0
        uids = await asyncio.to_thread(reader.uids_since, since)

    batch = [uid for uid in uids if uid > last_uid][: settings.mail_batch]
    place = FolderPlace(mailbox=username, folder=folder, uidvalidity=uidvalidity, sent=listed.sent)
    if not batch and not known:
        # Nothing to read: remember the folder as it is now, so the next poll asks only for newer
        # mail instead of the window again.
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

    room = settings.mail_batch - len(batch)
    if cursor is not None and known and room > 0:
        stored += await _read_history(
            reader,
            place,
            company_id,
            own_addresses,
            through=cursor.last_uid,
            since=since,
            read_since=cursor.history_since,
            limit=room,
        )
    return stored


async def _read_history(
    reader: MailboxReader,
    place: FolderPlace,
    company_id: uuid.UUID,
    own_addresses: set[str],
    *,
    through: int,
    since: date,
    read_since: date | None,
    limit: int,
) -> int:
    """Reads mail the poll has passed and not stored: what is older than the window the folder
    started with, once MAIL_BACKFILL_DAYS is raised. It walks back from the day the folder is read
    to (``history_since``), a month at a time, so the recent past arrives before the old whatever
    order the server numbered it in, and a poll asks only about the month it is reading. In each
    month it reads the mail that has no stored place; when none is left, the folder is read to
    that month's first day. So a crash loses nothing, and a folder read as far back as is wanted
    is not asked again until an earlier day is."""
    read_to = read_since or utcnow().date() + timedelta(days=1)
    room, stored = limit, 0
    for _ in range(HISTORY_MONTHS_A_POLL):
        if read_to <= since or room <= 0:
            break
        month = max(since, read_to - HISTORY_STEP)
        there = await asyncio.to_thread(reader.uids_between, through, month, read_to)
        async with open_session() as db:
            have = await repository.stored_uids(
                db, company_id, place.mailbox, place.folder, place.uidvalidity, there
            )
        missing = sorted(set(there) - have, reverse=True)
        for uid in missing[:room]:
            fetched = await asyncio.to_thread(reader.fetch, uid)
            async with open_session() as db:
                new = await ingest_mail(
                    db,
                    company_id=company_id,
                    place=place,
                    fetched=fetched,
                    own_addresses=own_addresses,
                )
                await db.commit()
            stored += int(new)
        if len(missing) > room:
            break  # the batch ran out inside this month: the next poll goes on with it
        room -= len(missing)
        async with open_session() as db:
            await repository.save_history(db, company_id, place.mailbox, place.folder, month)
            await db.commit()
        read_to = month
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
        if message.processed_at is not None:
            # Its thread was sorted without knowing the company wrote in it.
            await repository.note_mail(
                db, company_id, message.thread_key, outbound=True, bulk=message.bulk, at=utcnow()
            )
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
        bulk=parsed.bulk,
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
    they were stored: the sender's name, which files the body shows inline, the text a search
    looks in, and whether it is bulk mail; and asks for the files of a mail already read that
    an earlier rule left unread (the pictures its body shows). Returns the last
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
        message.bulk = parsed.bulk
        await _mark_inline(db, company_id, message.id, parsed)
        if message.processed_at is not None:
            await documents.request_reading(db, company_id, await _files_to_read(db, message))
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


# Processing what arrived


async def process_pending() -> int:
    """Processes the mail that waits, oldest first, each message in a transaction of its own, so one
    that fails leaves the rest processed. A failure is counted against its message, which is tried
    again next run, up to PROCESS_TRIES times, then once an hour. Returns how many were
    processed."""
    retry_before = utcnow() - PROCESS_RETRY_AFTER
    async with open_session() as db:
        company_ids = await repository.companies_with_pending(
            db, tries=PROCESS_TRIES, retry_before=retry_before
        )
    processed = 0
    for company_id in company_ids:
        async with open_session() as db:
            pending = await repository.pending_mail(
                db,
                company_id,
                tries=PROCESS_TRIES,
                retry_before=retry_before,
                limit=PROCESS_BATCH,
            )
        for message_id in pending:
            try:
                async with open_session() as db:
                    await process_message(db, company_id, message_id)
                    await events.publish(db, company_id, events.EventKind.THREADS)
                    await db.commit()
            except Exception as error:  # one mail that fails never stops the others
                # The error's type only: a database error's text can carry the values it wrote.
                logger.warning("processing message %s failed: %s", message_id, type(error).__name__)
                async with open_session() as db:
                    await repository.add_process_failure(db, company_id, message_id, utcnow())
                    await db.commit()
                continue
            processed += 1
    return processed


async def process_message(db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID) -> None:
    """Reads one stored message once: its own text, without the history it quotes, and the facts
    code finds in that text and its subject; and asks for its files to be read, the pictures its
    body shows too: one can be the message itself (a pasted screenshot, a photo of a plate), and
    one that is only a logo is left by its size or by how many conversations show it. A copy of
    a mail already read is marked as such and its text read no further; its attachments are
    still asked for, since a mailbox can keep a file another copy lost (a file both hold is one
    document, asked for once).
    Runs in the caller's transaction and does not commit."""
    message = await repository.get_message(db, company_id, message_id)
    if message is None or message.processed_at is not None:
        return
    original = await _processed_copy(db, message)
    if original is not None:
        message.copy_of_id = original.id
    else:
        message.own_text = quoted.own_text(message.body_text, message.subject)
        text = "\n".join(part for part in (message.subject, message.own_text) if part)
        await facts.record_facts(
            db,
            company_id=company_id,
            item_kind=ItemKind.MESSAGE,
            item_id=message.id,
            facts=extractors.from_text(text),
        )
    await documents.request_reading(db, company_id, await _files_to_read(db, message))
    message.processed_at = utcnow()
    await repository.note_mail(
        db,
        company_id,
        message.thread_key,
        outbound=message.direction == Direction.OUTBOUND,
        bulk=message.bulk,
        at=message.processed_at,
    )


async def reread_facts(
    db: AsyncSession,
    company_id: uuid.UUID,
    kinds: frozenset[str],
    *,
    after: uuid.UUID | None,
    limit: int = 200,
) -> tuple[int, uuid.UUID | None]:
    """Reads the facts of ``kinds`` again from one batch of read messages, for a kind code learned
    to find after they were read; the facts they have stay. Returns how many it found, and the
    last message read, to pass as ``after`` for the next batch (None when every one has been
    read). Does not commit."""
    batch = await repository.originals_after(db, company_id, after=after, limit=limit)
    found = 0
    for message in batch:
        text = "\n".join(part for part in (message.subject, message.own_text) if part)
        wanted = [fact for fact in extractors.from_text(text) if fact.kind in kinds]
        if wanted:
            await facts.record_facts(
                db,
                company_id=company_id,
                item_kind=ItemKind.MESSAGE,
                item_id=message.id,
                facts=wanted,
            )
            found += len({(fact.kind, fact.normalised) for fact in wanted})
    return found, batch[-1].id if batch else None


async def _files_to_read(db: AsyncSession, message: Message) -> list[uuid.UUID]:
    """The files of a mail to ask a reading of: those beside its text, and the pictures its body
    shows, since one can be the message itself (a pasted screenshot, a photo of a plate). Not
    the pictures of bulk mail, a newsletter's, nor one that several conversations already show,
    a sender's furniture; how small a picture holds nothing is the reader's to say (documents)."""
    attachments = await repository.attachments_of(db, message.company_id, message.id)
    shown = [file.document_id for file in attachments if file.inline]
    left = set(shown) if message.bulk else await _furniture(db, message.company_id, shown)
    return [file.document_id for file in attachments if file.document_id not in left]


async def _furniture(
    db: AsyncSession, company_id: uuid.UUID, shown: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these pictures, shown in a mail's body, so many conversations carry that they
    belong to none."""
    carried = await repository.conversations_carrying(db, company_id, shown)
    return {
        document_id for document_id, count in carried.items() if count >= _FURNITURE_CONVERSATIONS
    }


async def carried_files(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """The files these mails carried, each with every mail of the company that carried it as part
    of a job (ADR-0021): a file beside a mail's text, or a picture its body shows unless the mail
    is bulk mail or the picture a sender's furniture. A file so many conversations carry that it
    is standing paper comes back with no mail, so it leaves the projects it was in, and so does
    one that holds nothing of a job: a signature's picture, a logo, a picture not read yet (it
    joins once it is read with something on it). What an archive among them held rides with the
    archive."""
    if not message_ids:
        return {}
    attached = await repository.attachments_of_each(db, company_id, message_ids)
    files = sorted({file.document_id for each in attached.values() for file in each})
    spread = await repository.conversations_carrying(db, company_id, files)
    # Every file is answered, with no mail when none carries it as part of a job any more.
    carried: dict[uuid.UUID, list[uuid.UUID]] = {file: [] for file in files}
    kept = [file for file in files if spread.get(file, 0) <= _STANDING_CONVERSATIONS]
    members = await documents.members_of(db, company_id, kept)
    inside = {container_id: _within(container_id, members) for container_id in kept}
    nothing = await documents.holding_nothing(
        db, company_id, sorted({*kept, *(member for each in inside.values() for member in each)})
    )
    rows = await repository.carriers_of(db, company_id, kept)
    furniture = await _furniture(db, company_id, sorted({row[0] for row in rows if row[2]}))
    for document_id, message_id, inline, bulk in rows:
        if document_id not in nothing and (not inline or not (bulk or document_id in furniture)):
            carried[document_id].append(message_id)
    for container_id in kept:
        for member in inside[container_id]:
            ridden = [] if member in nothing else carried.get(container_id, [])
            carried[member] = sorted({*carried.get(member, []), *ridden})
    return carried


def _within(
    container_id: uuid.UUID, members: Mapping[uuid.UUID, list[uuid.UUID]]
) -> set[uuid.UUID]:
    """Every file an archive holds, those of an archive inside it too."""
    found: set[uuid.UUID] = set()
    waiting = list(members.get(container_id, []))
    while waiting:
        member = waiting.pop()
        if member not in found:
            found.add(member)
            waiting.extend(members.get(member, []))
    return found


async def _processed_copy(db: AsyncSession, message: Message) -> Message | None:
    """The same mail from another mailbox: its Message-ID, subject and text agree, while its
    delivery headers, and so its raw hash, differ. A sender that reuses a Message-ID sends another
    subject or text, whose facts are read too."""
    if message.rfc_message_id is None:
        return None
    for other in await repository.processed_copies(
        db, message.company_id, message.rfc_message_id, exclude=message.id
    ):
        if _as_read(other) == _as_read(message):
            return other
    return None


def _as_read(message: Message) -> tuple[str, str]:
    """What facts are read from, with runs of spaces made one."""
    return " ".join((message.subject or "").split()), " ".join(message.body_text.split())


# Sorting conversations


async def sort_pending() -> int:
    """Sorts the conversations that wait. Code sorts what a mail's own marks decide, for every
    company and at no cost: one the company wrote in is work, one of bulk mail only is noise. The
    ``intake`` agent sorts the rest, oldest first, within each company's budget of model reads for
    a day (MAIL_MODEL_READS_PER_DAY; at zero no mail's text leaves for the model's provider). A
    model that cannot be reached is counted against the conversation, which is tried again next
    run, up to SORT_TRIES times, then once an hour. Returns how many were sorted."""
    now = utcnow()
    retry_before = now - SORT_RETRY_AFTER
    budget = get_settings().mail_model_reads_per_day
    async with open_session() as db:
        company_ids = await repository.companies_with_unsorted(
            db, tries=SORT_TRIES, retry_before=retry_before
        )
    sorted_count = 0
    for company_id in company_ids:
        async with open_session() as db:
            by_rules = await repository.sort_by_rules(db, company_id, now)
            if by_rules:
                await events.publish(db, company_id, events.EventKind.THREADS)
            await db.commit()
            sorted_count += by_rules
            left = budget - await repository.model_reads_since(db, company_id, now - SORT_WINDOW)
            waiting = await repository.unsorted_threads(
                db,
                company_id,
                tries=SORT_TRIES,
                retry_before=retry_before,
                limit=max(min(SORT_BATCH, left), 0),
            )
            company = (await companies.get_companies(db, [company_id])).get(company_id)
        trace_content = company is not None and companies.traces_content(company.kind)
        language = company.language if company is not None else Language.EN
        for thread_key in waiting:
            try:
                await sort_with_model(
                    company_id, thread_key, language=language, trace_content=trace_content
                )
            except Exception as error:  # one conversation that fails never stops the others
                # The error's type only: its text can carry what the mail holds.
                logger.warning("sorting a conversation failed: %s", type(error).__name__)
                async with open_session() as db:
                    await repository.add_sort_failure(db, company_id, thread_key, utcnow())
                    await db.commit()
                continue
            await events.publish_now(company_id, [events.EventKind.THREADS])
            sorted_count += 1
    return sorted_count


async def sort_with_model(
    company_id: uuid.UUID, thread_key: str, *, language: Language, trace_content: bool
) -> None:
    """Has the ``intake`` agent sort one conversation from its first mails, in a call with no
    tools (ADR-0013). An answer that cannot be used leaves the conversation as work, marked as
    unanswered: reading one conversation too many costs less than dropping a customer's mail. No
    transaction is open while the model reads. The read is counted before it is made, so a call
    that fails once the provider has the mail still counts against the budget."""
    async with open_session() as db:
        excerpt = await _excerpt(db, company_id, thread_key)
        await repository.add_model_read(db, company_id, agents.INTAKE, utcnow())
        await db.commit()
    try:
        run = await llm.run(
            agents.intake,
            agents.conversation(excerpt),
            deps=None,
            limits=_SORT_LIMITS,
            language=language,
            trace_content=trace_content,
        )
        category, reason, sorted_by = run.output.category, run.output.reason, agents.MODEL_SORTED
        requests = run.usage.requests
    except llm.NoAnswer as error:
        category, reason, sorted_by = ThreadCategory.WORK, "", repository.UNANSWERED
        requests = error.requests
    async with open_session() as db:
        # A request made again after an answer failed its checks sent the mail again.
        for _ in range(requests - 1):
            await repository.add_model_read(db, company_id, agents.INTAKE, utcnow())
        await repository.set_sorting(
            db,
            company_id,
            thread_key,
            category=category,
            sorted_by=sorted_by,
            reason=" ".join(reason.replace("\x00", "").split()),
            at=utcnow(),
        )
        await db.commit()


async def _excerpt(db: AsyncSession, company_id: uuid.UUID, thread_key: str) -> str:
    """The start of a conversation as the model is shown it: for each of its first mails who wrote
    it, its subject, the names of the files beside it, and the start of its own text."""
    parts = []
    mails = await repository.first_mail(db, company_id, thread_key, limit=_SORT_MAILS)
    attached = await repository.attachments_of_each(db, company_id, [mail.id for mail in mails])
    for message in mails:
        files = [file.filename for file in attached.get(message.id, []) if not file.inline]
        sender = " ".join(part for part in (message.sender_name, f"<{message.sender}>") if part)
        lines = [
            f"From: {sender}" + (" (the company itself)" if _outbound(message) else ""),
            f"Subject: {message.subject or ''}",
        ]
        if files:
            lines.append("Files: " + ", ".join(files))
        lines.append((message.own_text or message.body_text)[:_SORT_TEXT].strip())
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)


def _outbound(message: Message) -> bool:
    return message.direction == Direction.OUTBOUND


# For what reads conversations next


@dataclass(frozen=True, slots=True)
class FileRead:
    document_id: uuid.UUID
    filename: str


@dataclass(frozen=True, slots=True)
class MailRead:
    id: uuid.UUID
    sent_at: datetime
    sender: str | None
    sender_name: str | None
    # Written by the company itself.
    outbound: bool
    subject: str | None
    # What its writer typed, without the history it quotes.
    text: str
    # The files beside its text, and the pictures its body shows that are large enough to hold
    # writing and are this conversation's own; a logo or a signature is not one.
    files: list[FileRead]


@dataclass(frozen=True, slots=True)
class ThreadRead:
    """A conversation as it was read: its mails oldest first, a copy in another mailbox once."""

    id: uuid.UUID
    category: ThreadCategory | None
    changed_at: datetime
    mails: list[MailRead]


async def mailbox_domains(db: AsyncSession, company_id: uuid.UUID) -> set[str]:
    """The domains of the mailboxes Tamga reads for the company. Each was signed in to, so each is
    the company's own."""
    mailboxes = await repository.mailboxes(db, company_id)
    found = (maildomains.address_domain(mailbox) for mailbox in mailboxes)
    return {domain for domain in found if domain is not None}


async def mail_by_sender_domain(
    db: AsyncSession, company_id: uuid.UUID, domains: Sequence[str] | None = None
) -> dict[str, DomainMail]:
    """How many mails the company sent from each sender domain and how many it received from it,
    by the domain's IDNA form. With no ``domains``, every domain it sent from: its own mailboxes'
    Sent folders hold them."""
    asked = None if domains is None else [way for d in domains for way in maildomains.spellings(d)]
    found: dict[str, DomainMail] = {}
    for row in await repository.mail_by_sender_domain(db, company_id, asked):
        # One domain stored in two spellings is one domain, in its IDNA form.
        domain = maildomains.domain_of(row.domain)
        if domain is None:
            continue
        held = found.get(domain)
        found[domain] = DomainMail(
            domain,
            row.sent + (held.sent if held else 0),
            row.received + (held.received if held else 0),
        )
    return found


async def outbound_among(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these messages the company sent."""
    return await repository.outbound_among(db, company_id, ids) if ids else set()


async def companies_with_read_threads(db: AsyncSession) -> list[uuid.UUID]:
    return await repository.companies_with_read_threads(db)


async def changed_threads(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    since: datetime,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[tuple[uuid.UUID, datetime]]:
    """The conversations sorted as work or automated that got mail or were sorted at or after
    ``since``, each with when, in that order; ``after`` is the last of the page before."""
    rows = await repository.changed_threads(db, company_id, since=since, after=after, limit=limit)
    return [(row.id, row.changed_at) for row in rows]


async def read_thread(
    db: AsyncSession, company_id: uuid.UUID, thread_id: uuid.UUID
) -> ThreadRead | None:
    thread = await repository.get_thread_by_id(db, company_id, thread_id)
    if thread is None:
        return None
    return (await _as_read_threads(db, company_id, [thread]))[0]


async def search_threads(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[ThreadRead]:
    """Conversations sorted as work or automated holding a mail that matches ``q`` (as the app's
    search matches) and was sent in the span, most recently active first. Noise is never found."""
    threads = await repository.find_threads(
        db, company_id, q=q, since=since, until=until, limit=limit
    )
    return await _as_read_threads(db, company_id, threads)


async def thread_ids_of(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """The conversation each of these mails is in."""
    if not message_ids:
        return {}
    return await repository.thread_ids_of(db, company_id, message_ids)


async def threads_holding(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID], *, limit: int
) -> list[ThreadRead]:
    """The conversations these mails are in, most recently changed first."""
    threads = await repository.threads_of_messages(db, company_id, message_ids, limit=limit)
    return await _as_read_threads(db, company_id, threads)


async def message_summaries(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, MessageSummary]:
    """These mails as a list row shows them; one that is gone is left out."""
    if not ids:
        return {}
    rows = await repository.messages_by_ids(db, company_id, ids)
    return {message.id: _summary(message, count) for message, count in rows}


@dataclass(frozen=True, slots=True)
class MailLabel:
    """A mail as a line naming where something was read shows it."""

    subject: str | None
    # The writer's name, else their address.
    writer: str | None
    sent_at: datetime


async def mail_labels(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, MailLabel]:
    """These mails' subjects, writers and days alone, never their text."""
    if not ids:
        return {}
    rows = await repository.mail_labels(db, company_id, ids)
    return {
        mail_id: MailLabel(subject, name or sender, sent_at)
        for mail_id, subject, name, sender, sent_at in rows
    }


@dataclass(frozen=True, slots=True)
class Carrier:
    """The first mail that brought a file."""

    message_id: uuid.UUID
    sent_at: datetime
    channel: Channel


async def first_carriers(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Carrier]:
    """For each of these files, the first mail that brought it, itself or inside an archive. A
    file no mail brought (an upload) is left out."""
    if not document_ids:
        return {}
    outer = await documents.containers_of_each(db, company_id, document_ids)
    every = {*document_ids, *(each for containers in outer.values() for each in containers)}
    first = {
        document_id: Carrier(message_id=message_id, sent_at=sent_at, channel=channel)
        for document_id, message_id, sent_at, channel in await repository.first_carriers(
            db, company_id, list(every)
        )
    }
    found = {}
    for document_id in document_ids:
        # The file came first in whichever came first: itself, or an archive that held it.
        carriers = [first[each] for each in (document_id, *outer[document_id]) if each in first]
        if carriers:
            found[document_id] = min(carriers, key=lambda carrier: carrier.sent_at)
    return found


async def messages_attaching(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """For each of these files, the mails that carried it."""
    carried: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in await repository.messages_attaching(db, company_id, document_ids):
        carried.setdefault(row.document_id, []).append(row.message_id)
    return carried


async def _as_read_threads(
    db: AsyncSession, company_id: uuid.UUID, threads: Sequence[Thread]
) -> list[ThreadRead]:
    mails = await repository.mail_as_read(db, company_id, [thread.thread_key for thread in threads])
    attached = await repository.attachments_of_each(db, company_id, [mail.id for mail in mails])
    shown = [file.document_id for files in attached.values() for file in files if file.inline]
    pictures = await documents.for_the_vision_model(db, company_id, shown)
    pictures -= await _furniture(db, company_id, shown)
    by_key: dict[str, list[MailRead]] = {}
    for mail in mails:
        by_key.setdefault(mail.thread_key, []).append(
            MailRead(
                id=mail.id,
                sent_at=mail.sent_at,
                sender=mail.sender,
                sender_name=mail.sender_name,
                outbound=_outbound(mail),
                subject=mail.subject,
                # A reply of nothing but quoted history has no text of its own.
                text=mail.own_text,
                files=[
                    FileRead(document_id=file.document_id, filename=file.filename)
                    for file in attached.get(mail.id, [])
                    if not file.inline or file.document_id in pictures
                ],
            )
        )
    return [
        ThreadRead(
            id=thread.id,
            category=thread.category,
            changed_at=thread.changed_at,
            mails=by_key.get(thread.thread_key, []),
        )
        for thread in threads
    ]


async def model_reads_left(db: AsyncSession, company_id: uuid.UUID) -> int:
    """What is left of the company's budget of model reads for a day (MAIL_MODEL_READS_PER_DAY),
    which every agent that is sent its mail shares."""
    spent = await repository.model_reads_since(db, company_id, utcnow() - SORT_WINDOW)
    return get_settings().mail_model_reads_per_day - spent


async def model_reads_since(db: AsyncSession, company_id: uuid.UUID, since: datetime) -> int:
    """How many requests sent the company's mail to a model since then, by every agent."""
    return await repository.model_reads_since(db, company_id, since)


async def count_model_reads(
    db: AsyncSession, company_id: uuid.UUID, agent: str, *, requests: int = 1
) -> None:
    """Counts requests in which ``agent`` was sent the company's mail against that budget. Does
    not commit."""
    for _ in range(requests):
        await repository.add_model_read(db, company_id, agent, utcnow())


# Reading what arrived


async def message_exists(db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID) -> bool:
    return await repository.message_exists(db, company_id, message_id)


async def _attaches(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    return await repository.attaches(db, company_id, document_id)


# A file a mail brought stays as long as the mail does: it cannot be deleted from the files page.
documents.register_holder(Holder.MAIL, _attaches)


async def _file_was_read(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    carried = await repository.conversations_carrying(db, company_id, [document_id])
    if carried.get(document_id, 0) > _COMMON_CONVERSATIONS:
        return
    await repository.touch_threads_carrying(db, company_id, document_id, utcnow())


# A file is often read after its mail: what reads conversations next sees the conversation as
# changed once more is known of one of its files.
documents.on_read(_file_was_read)


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
    items = [_thread_summary(row.thread_key, by_thread[row.thread_key]) for row in rows]
    return ThreadPage(items=items, total=total, limit=page.limit, offset=page.offset)


def _thread_summary(thread_key: str, mails: list[tuple[Message, int]]) -> ThreadSummary:
    """A conversation from its mails, oldest first, each with how many files it carried."""
    once = _once(mails)
    participants = dict.fromkeys(
        message.sender_name or message.sender for message, _ in mails if message.sender
    )
    latest, latest_count = mails[-1]
    return ThreadSummary(
        thread_key=thread_key,
        subject=mails[0][0].subject,
        message_count=len(once),
        attachment_count=sum(count for _, count in once),
        participants=[name for name in participants if name],
        first_sent_at=mails[0][0].sent_at,
        last_sent_at=latest.sent_at,
        latest=_summary(latest, latest_count),
    )


@dataclass(frozen=True, slots=True)
class ThreadBrief:
    """A conversation as a list shows it, and the mails it holds."""

    summary: ThreadSummary
    mail_ids: list[uuid.UUID]


async def thread_briefs(
    db: AsyncSession, company_id: uuid.UUID, thread_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ThreadBrief]:
    """These conversations by id; one whose mail is gone is left out."""
    threads = await repository.threads_by_ids(db, company_id, thread_ids)
    by_key: dict[str, list[tuple[Message, int]]] = {}
    for message, count in await repository.thread_messages(
        db, company_id, [thread.thread_key for thread in threads]
    ):
        by_key.setdefault(message.thread_key, []).append((message, count))
    return {
        thread.id: ThreadBrief(
            summary=_thread_summary(thread.thread_key, by_key[thread.thread_key]),
            mail_ids=[message.id for message, _ in by_key[thread.thread_key]],
        )
        for thread in threads
        if thread.thread_key in by_key
    }


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
    if shown is None:
        winmail = next(
            (
                attachment.document_id
                for attachment in attachments
                if documents.is_outlook_wrapping(
                    files[attachment.document_id].content_type, attachment.filename
                )
            ),
            None,
        )
        if winmail is not None:
            wrapped = await _outlook_html(db, actor.company_id, message.id, winmail)
            if wrapped is not None:
                shown = wrapped.shown
                stored |= wrapped.stored
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


async def _html_of(message: Message) -> SafeHtml | None:
    """The mail's HTML body, sanitized as it is read: a stricter sanitizer needs no backfill."""
    if message.raw_key is None:
        return None
    try:
        return await asyncio.to_thread(html.render, await storage.get(message.raw_key))
    except Exception:  # an unreachable original or a broken body; the stored text is still shown
        logger.exception("the HTML of mail %s could not be read", message.id)
        return None


@dataclass(frozen=True, slots=True)
class _Wrapped:
    shown: SafeHtml
    # The stored files that hold the pictures it draws, by the SHA-256 of their bytes.
    stored: dict[bytes, uuid.UUID]


async def _outlook_html(
    db: AsyncSession, company_id: uuid.UUID, message_id: uuid.UUID, winmail: uuid.UUID
) -> _Wrapped | None:
    """The body of a mail Outlook sent as "rich text": its own parts hold only bare text, and what
    the sender laid out is inside its ``winmail.dat``. Sanitized like any mail's HTML."""
    try:
        body = await documents.outlook_body(db, company_id, winmail)
    except Exception:  # an unreachable file; the stored text is still shown
        logger.exception("the winmail.dat of mail %s could not be read", message_id)
        return None
    if body is None:
        return None
    shown = await asyncio.to_thread(sanitize, body.html, body.carried)
    return _Wrapped(shown=shown, stored=body.stored)


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
