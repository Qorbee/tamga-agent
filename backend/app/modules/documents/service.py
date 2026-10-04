"""Documents: files stored once per company by content (ADR-0001 storage, private), what kind of
document each is (the registry), the files people upload by hand, and what each file says, read in
a locked child process (ADR-0012) without what its viewer hides (ADR-0013)."""

import asyncio
import base64
import dataclasses
import hashlib
import logging
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import PurePosixPath
from typing import Final

from pydantic import BaseModel, ValidationError
from pydantic_ai import UsageLimits
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.documents import agents, reading, registry, repository, unpacking
from app.modules.documents.models import Document, DocumentPart, ReadState, TypeSource, Upload
from app.modules.documents.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentPage,
    DocumentPartOut,
    DocumentTypeOut,
    DocumentUpdate,
    Holder,
    HtmlOut,
    TextOut,
    UploadOut,
    WorkbookOut,
)
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.platform import (
    converter,
    events,
    filetypes,
    html,
    isolated,
    jobs,
    llm,
    search,
    storage,
)
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound, PayloadTooLarge
from app.platform.items import ItemKind
from app.platform.language import Language
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

_UPLOADERS: Final = (Role.OWNER, Role.OPS)
# What holds a file under the name it came with (a project names nothing): that file keeps it.
_NAMING: Final = frozenset({Holder.MAIL, Holder.ARCHIVE})
# Files one company has read per run; a child per file, the next run a minute later takes the rest.
READ_BATCH: Final = 20
# How many times a file is tried each run when reading fails outside the file (storage, the
# database) before it waits an hour between tries.
READ_TRIES: Final = 3
READ_RETRY_AFTER: Final = timedelta(hours=1)
# What is kept of a file's text: a hundred pages of dense print.
MAX_TEXT: Final = 1_000_000
# A page with less visible text than this is a scan, or mostly one.
_SCANNED_PAGE: Final = 20
# The largest web page, XML or text file read or shown; past it, it is only downloaded.
MAX_TEXT_FILE: Final = 10 * 1024 * 1024
# Files one company has read by the vision model per run: a call each, the next run takes the rest.
VISION_BATCH: Final = 10
# The budget of pictures (VISION_PAGES_PER_DAY) counts those read in this long.
VISION_WINDOW: Final = timedelta(days=1)
# Files the typist is asked about in one run for one company, and the window its budget counts.
TYPING_BATCH: Final = 50
TYPING_WINDOW: Final = timedelta(days=1)
# One answer, and one more request when it names a type it was not offered.
_TYPING_LIMITS: Final = UsageLimits(request_limit=2)
# The largest photo sent to the vision model.
MAX_PICTURE: Final = 20 * 1024 * 1024
# The picture types the vision model takes as they are; a BMP it does not.
_SHOWN_AS_IT_IS: Final = frozenset(filetypes.IMAGES - {filetypes.BMP})
# A model can misread a character that a pattern then accepts, so a fact found in what it wrote is
# never surer than this, nor than the pattern's own confidence.
_MODEL_CONFIDENCE: Final = 0.8
# One answer, and two more requests when it fails the output's checks.
_VISION_LIMITS: Final = UsageLimits(request_limit=3)
# A picture shorter than this on a side, or of fewer pixels in all, holds no page, screenshot or
# photo: an icon, a logo, a line of a signature. It is not shown to a model.
_PICTURE_SIDE: Final = 80
_PICTURE_PIXELS: Final = 40_000

# The runs that take files out of an archive read up to 200 MB of it, so each gets two minutes.
_UNPACK_LIMITS: Final = isolated.Limits(deadline_seconds=120.0)

# Whether a module that keeps documents holds this one (a mail's attachments, a project's evidence).
type HoldsCheck = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[bool]]
_holders: dict[Holder, HoldsCheck] = {}
# Told when a document was read: the database session, the company and the document.
type ReadListener = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[None]]
_read_listeners: list[ReadListener] = []
_unpack_listeners: list[ReadListener] = []
_known_listeners: list[ReadListener] = []
# The DOS code page a country's Windows writes a zip's names in, when not as UTF-8 (ADR-0012).
_codepages: dict[str, str] = {}


def register_holder(holder: Holder, holds: HoldsCheck) -> None:
    """A module that keeps documents says, through ``holds``, whether it keeps one. Only a file no
    holder keeps can be deleted."""
    _holders[holder] = holds


def on_unpacked(listener: ReadListener) -> None:
    """A module that keeps files with what carried them is told, in the unpacking's own
    transaction, when an archive's files were taken out: they ride with the archive. Not the read
    listeners: an archive holds no text of its own to look at again."""
    _unpack_listeners.append(listener)


def on_read(listener: ReadListener) -> None:
    """A module that keeps documents asks to be told, in the reading's own transaction, when one
    was read: what rests on a file's facts looks at it again."""
    _read_listeners.append(listener)


def on_known(listener: ReadListener) -> None:
    """A module that keeps files by what they hold asks to be told, in the same transaction,
    when more is known of one after it came: it was read, found unreadable, or typed by the
    typist or the owner."""
    _known_listeners.append(listener)


async def _became_known(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    for listener in _known_listeners:
        await listener(db, company_id, document_id)


async def _was_read(db: AsyncSession, document: Document) -> None:
    document.read_state, document.read_at = ReadState.READ, utcnow()
    await _classify(db, document)
    for listener in _read_listeners:
        await listener(db, document.company_id, document.id)
    await _became_known(db, document.company_id, document.id)


async def _classify(db: AsyncSession, document: Document) -> None:
    """Types a file nobody typed by what is plainly written in it, before anything that rests on
    its type hears of it. Written only where the stored row is still untyped: a type the owner
    saved while the file was being read stands."""
    if document.doc_type is not None:
        return
    key = registry.classify(
        registry.Seen(filename=document.filename, text=search.fold(document.text))
    )
    if key is not None and await repository.type_if_untyped(
        db, document.company_id, document.id, key, TypeSource.CODE
    ):
        set_committed_value(document, "doc_type", key)
        set_committed_value(document, "doc_type_source", TypeSource.CODE)


async def classify_read(db: AsyncSession, company_id: uuid.UUID) -> dict[str, int]:
    """Types by code every file of the company read before the rules existed, and counts them
    by type. Does not commit."""
    counts: dict[str, int] = {}
    for document in await repository.untyped_read(db, company_id):
        await _classify(db, document)
        if document.doc_type is not None:
            counts[document.doc_type] = counts.get(document.doc_type, 0) + 1
    return counts


def register_name_codepage(country: str, codepage: str) -> None:
    """A country pack names the code page its Windows writes a zip's names in (Turkey: cp857)."""
    _codepages[country] = codepage


# A file taken out of an archive stays as long as the archive does.
register_holder(Holder.ARCHIVE, repository.is_part)


async def store_file(
    db: AsyncSession, *, company_id: uuid.UUID, data: bytes, filename: str, content_type: str
) -> Document:
    """Writes the bytes to storage and returns the company's document for them. Runs inside the
    caller's transaction and does not commit. If that transaction rolls back, the object stays in
    storage under its content key, where the next attempt finds it again."""
    sha256 = hashlib.sha256(data).digest()
    await repository.lock_content(db, company_id, sha256)
    existing = await repository.get_by_hash(db, company_id, sha256)
    if existing is not None:
        _learn_kind(existing, filename, content_type)
        return existing
    key = storage.content_key(company_id, data)
    await storage.put(key, data, content_type=content_type)
    return await repository.insert_unless_stored(
        db,
        company_id=company_id,
        sha256=sha256,
        storage_key=key,
        filename=filename[:300],
        content_type=content_type[:200],
        size=len(data),
    )


def _learn_kind(document: Document, filename: str, content_type: str) -> None:
    """A file first stored under a name and type that say nothing (``ATT00001.bin``,
    ``application/octet-stream``) takes the type a later name gives it (``invoice.pdf`` in an
    archive), so it is shown and read as what it is; one left unsupported for want of it is read
    again. Its name stays: the owner may have chosen it."""
    if filetypes.preview(document.content_type, document.filename) is not None:
        return
    known = filetypes.archive_type(content_type, filename) or filetypes.media_type(
        content_type, filename
    )
    if known is None:
        return
    document.content_type = known
    if document.unreadable_reason == isolated.Reason.UNSUPPORTED:
        document.read_state, document.unreadable_reason = ReadState.PENDING, None
        document.read_at, document.read_failures, document.read_failed_at = None, 0, None


async def upload(
    db: AsyncSession,
    actor: Actor,
    *,
    chunks: AsyncIterator[bytes],
    filename: str,
    content_type: str,
    doc_type: str | None,
    note: str,
) -> UploadOut:
    """A file from the upload page. Read up to the size limit and refused past it, so a huge body
    never sits in memory whole."""
    actor.require(*_UPLOADERS)
    filename = filename.strip()
    if not filename:
        raise BadRequest("Name the file", field="filename")
    _check_type(doc_type)
    data = await _read_limited(chunks, get_settings().upload_max_bytes)
    if not data:
        raise BadRequest("The file is empty")
    had = await repository.get_by_hash(db, actor.company_id, hashlib.sha256(data).digest())
    document = await store_file(
        db,
        company_id=actor.company_id,
        data=data,
        filename=filename,
        content_type=content_type or "application/octet-stream",
    )
    # The uploader's type fills an unknown one, or one code or a model guessed; it never replaces
    # a type a person gave.
    if doc_type is not None and document.doc_type_source is not TypeSource.OWNER:
        document.doc_type, document.doc_type_source = doc_type, TypeSource.OWNER
    row = Upload(
        company_id=actor.company_id,
        document_id=document.id,
        uploaded_by=actor.user_id,
        filename=filename[:300],
        note=note,
    )
    db.add(row)
    await request_reading(db, actor.company_id, [document.id])
    await db.flush()
    await db.commit()
    return UploadOut(
        id=row.id,
        document=_out(document),
        filename=row.filename,
        note=row.note,
        new_document=had is None,
        created_at=row.created_at,
    )


async def list_documents(
    db: AsyncSession,
    actor: Actor,
    *,
    q: str | None,
    doc_type: str | None,
    untyped: bool,
    members: bool,
    page: PageParams,
) -> DocumentPage:
    rows, total = await repository.list_documents(
        db,
        actor.company_id,
        q=q.strip() if q else None,
        doc_type=doc_type,
        untyped=untyped,
        members=members,
        page=page,
    )
    return DocumentPage(
        items=[_out(row) for row in rows], total=total, limit=page.limit, offset=page.offset
    )


async def get_document(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> DocumentDetail:
    document = await _document(db, actor.company_id, document_id)
    return DocumentDetail(
        **_out(document).model_dump(),
        download_url=storage.presign(document.storage_key, filename=document.filename),
        held_by=await _held_by(db, actor.company_id, document_id),
    )


async def _held_by(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> list[Holder]:
    return [
        holder for holder, holds in _holders.items() if await holds(db, company_id, document_id)
    ]


@dataclass(frozen=True, slots=True)
class FileContent:
    data: bytes
    filename: str
    # The verified type when the browser may show the file itself; else application/octet-stream.
    media_type: str
    inline: bool


async def get_content(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> FileContent:
    """The file's bytes, for the web app to draw. An image or a PDF whose first bytes prove it is
    shown; anything else is only ever a download (filetypes)."""
    document = await _document(db, actor.company_id, document_id)
    data = await storage.get(document.storage_key)
    shown_as = filetypes.inline_type(document.content_type, document.filename, data)
    return FileContent(
        data=data,
        filename=document.filename,
        media_type=shown_as or "application/octet-stream",
        inline=shown_as is not None,
    )


async def get_rendition(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> FileContent:
    """The PDF the converter made of an office file, shown in its place."""
    document = await _document(db, actor.company_id, document_id)
    if document.rendition_key is None:
        raise NotFound("The document has no PDF made of it")
    return FileContent(
        data=await storage.get(document.rendition_key),
        filename=f"{PurePosixPath(document.filename).stem}.pdf",
        media_type=filetypes.PDF,
        inline=True,
    )


async def get_html(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> HtmlOut:
    """A web page made safe to show, as a mail's body is (platform/html.py)."""
    decoded = await _text_file(db, actor, document_id, {filetypes.HTML})
    # Without its styles and colours, as it is read: nothing in it is hidden from the reader.
    shown = await asyncio.to_thread(partial(html.sanitize, decoded, styled=False))
    return HtmlOut(html=shown.html, remote_images=shown.remote_images)


async def get_text(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> TextOut:
    """An XML or text file's content, to show as text."""
    decoded = await _text_file(db, actor, document_id, {filetypes.XML, filetypes.TEXT})
    return TextOut(text=decoded[:MAX_TEXT], truncated=len(decoded) > MAX_TEXT)


async def _text_file(
    db: AsyncSession, actor: Actor, document_id: uuid.UUID, kinds: set[str]
) -> str:
    document = await _document(db, actor.company_id, document_id)
    if filetypes.media_type(document.content_type, document.filename) not in kinds:
        raise BadRequest("The document is not a file shown this way")
    if document.size > MAX_TEXT_FILE:
        raise BadRequest("The file is too large to show")
    data = await storage.get(document.storage_key)
    decoded = filetypes.decode_text(data, filetypes.charset(document.content_type))
    if decoded is None:
        raise BadRequest("The file is not text")
    return decoded


async def get_parts(
    db: AsyncSession, actor: Actor, document_id: uuid.UUID
) -> list[DocumentPartOut]:
    """The files taken out of an archive, in their order there; none before it is read."""
    await _document(db, actor.company_id, document_id)
    parts = await repository.parts_of(db, actor.company_id, document_id)
    members = await get_documents(
        db, actor.company_id, [part.document_id for part in parts if part.document_id]
    )
    return [
        DocumentPartOut(
            position=part.position,
            name=part.name,
            size=part.size,
            document=_out(members[part.document_id]) if part.document_id in members else None,
            unreadable_reason=part.unreadable_reason,
        )
        for part in parts
    ]


async def get_sheets(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> WorkbookOut:
    document = await _document(db, actor.company_id, document_id)
    media = filetypes.media_type(document.content_type, document.filename)
    if media is None or media not in filetypes.SHEETS:
        raise BadRequest("The document is not a spreadsheet")
    data = await storage.get(document.storage_key)
    outcome = await isolated.run("documents.workbook", data, media)
    if outcome.refused == isolated.Reason.TOO_LARGE:
        raise BadRequest("The spreadsheet is too large to show")
    if outcome.refused is not None:
        raise BadRequest("The spreadsheet cannot be read")
    # What the child sent is checked like any input from outside.
    try:
        return WorkbookOut.model_validate(outcome.value)
    except ValidationError as error:
        raise BadRequest("The spreadsheet cannot be read") from error


def preview_of(content_type: str, filename: str) -> filetypes.Preview | None:
    """How the web app shows a file under this name, for a module listing its own attachments."""
    return filetypes.preview(content_type, filename)


@dataclass(frozen=True, slots=True)
class OutlookBody:
    """The body Outlook wrapped inside a mail's ``winmail.dat``, for the module that shows mail."""

    # HTML from outside: not yet sanitized.
    html: str
    # The pictures it carries under a Content-ID that Tamga has taken out and stored: each by the
    # SHA-256 of its bytes, and the stored file that holds those bytes.
    carried: dict[str, bytes]
    stored: dict[bytes, uuid.UUID]


def is_outlook_wrapping(content_type: str, filename: str) -> bool:
    return filetypes.archive_type(content_type, filename) == filetypes.TNEF


async def outlook_body(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID
) -> OutlookBody | None:
    """The HTML a mail's ``winmail.dat`` holds, read in a child; None when it holds none (its
    sender wrote rich text) or cannot be read. The mail's own text part is then what is shown."""
    document = await _document(db, company_id, document_id)
    data = await storage.get(document.storage_key)
    body = _checked(unpacking.MailBody, await isolated.run("documents.tnef_body", data, ""))
    if isinstance(body, isolated.Reason) or not body.html:
        return None
    parts = await repository.parts_of(db, company_id, document_id)
    members = await repository.get_many(
        db, company_id, [part.document_id for part in parts if part.document_id is not None]
    )
    by_digest = {member.sha256: member.id for member in members}
    # A picture counts only once it is a stored file of this winmail.dat: the child's word alone
    # names nothing the web app would load.
    carried = {
        cid: digest
        for cid, claimed in body.images.items()
        if (digest := _digest(claimed)) in by_digest
    }
    return OutlookBody(
        html=body.html,
        carried=carried,
        stored={digest: by_digest[digest] for digest in carried.values()},
    )


def _digest(hex_text: str) -> bytes:
    try:
        return bytes.fromhex(hex_text)
    except ValueError:
        return b""


async def update_document(
    db: AsyncSession, actor: Actor, document_id: uuid.UUID, body: DocumentUpdate
) -> DocumentOut:
    actor.require(*_UPLOADERS)
    document = await _document(db, actor.company_id, document_id)
    if "doc_type" in body.model_fields_set:
        _check_type(body.doc_type)
        document.doc_type = body.doc_type
        # Cleared, the type is unknown again, and code may tell it next time the file is read.
        document.doc_type_source = TypeSource.OWNER if body.doc_type is not None else None
        await db.flush()
        await _became_known(db, actor.company_id, document.id)
    if body.filename is not None:
        name = _new_name(document.filename, body.filename)
        await _check_renamable(db, actor.company_id, document)
        document.filename = name
    await db.commit()
    return _out(document)


async def _check_renamable(db: AsyncSession, company_id: uuid.UUID, document: Document) -> None:
    """A file a mail or an archive brought keeps the name it came with: it is the one name for it
    everywhere, and the sender's. Only a file people put in themselves is theirs to name."""
    # A mail storing the same file waits on the content lock and a new link on the row's, so
    # neither lands between the check and the rename.
    await repository.lock_content(db, company_id, document.sha256)
    if await repository.lock_document(db, company_id, document.id) is None:
        raise NotFound("No such document")
    if any(holder in _NAMING for holder in await _held_by(db, company_id, document.id)):
        raise Conflict(
            "A file that came in a mail or an archive keeps the name it came with", field="filename"
        )


def _new_name(old: str, new: str) -> str:
    """A name to show the file under. Its extension says what kind of file it is (a file with a
    vague declared type is shown and read by it), so a new name keeps the old one's."""
    name = new.strip()
    if not name or any(unicodedata.category(char) == "Cc" for char in name):
        raise BadRequest("Name the file", field="filename")
    extension = filetypes.extension(old)
    if filetypes.extension(name).lower() != extension.lower():
        raise BadRequest(f"Keep the file's extension ({extension or 'none'})", field="filename")
    return name


async def delete_document(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> None:
    """Deletes an uploaded file, with what was read from it, and, for an archive, the files taken
    out of it that nothing else keeps. A file a mail brought, an archive holds or a project links
    is refused (``held_by``). The stored bytes go in a job after the commit, unless the same file
    has been stored again by then."""
    actor.require(*_UPLOADERS)
    document = await _document(db, actor.company_id, document_id)
    if not await _delete(db, actor.company_id, document, uploads_hold=False):
        raise Conflict("Only an uploaded file that nothing else keeps can be deleted")
    await db.commit()


async def _delete(
    db: AsyncSession, company_id: uuid.UUID, document: Document, *, uploads_hold: bool
) -> bool:
    """Deletes the document unless something holds it (and, with ``uploads_hold``, unless an
    upload does), then the members only it held. False when it is kept."""
    # A mail storing the same file waits on the content lock and a new link on the row's, so
    # neither lands between the check and the delete: a mail's attachment row would cascade away.
    await repository.lock_content(db, company_id, document.sha256)
    if await repository.lock_document(db, company_id, document.id) is None:
        return False
    if await _held_by(db, company_id, document.id):
        return False
    if uploads_hold and await repository.has_uploads(db, company_id, document.id):
        return False
    # Once each: the same file can sit in an archive under two names.
    members = list(
        dict.fromkeys(
            part.document_id
            for part in await repository.parts_of(db, company_id, document.id)
            if part.document_id is not None and part.document_id != document.id
        )
    )
    stored = {
        "company_id": str(company_id),
        "sha256": document.sha256.hex(),
        "storage_key": document.storage_key,
    }
    await facts.forget_item(
        db, company_id=company_id, item_kind=ItemKind.DOCUMENT, item_id=document.id
    )
    # Its parts rows go with it, so its members are held by one archive fewer.
    await repository.delete_document(db, company_id, document.id)
    # tasks imports this module, so the job is found here, where it is deferred.
    from app.modules.documents import tasks

    await jobs.defer(db, tasks.forget_file, args=stored)
    for member in await repository.get_many(db, company_id, members):
        await _delete(db, company_id, member, uploads_hold=True)
    return True


async def forget_file(company_id: uuid.UUID, sha256: bytes, storage_key: str) -> None:
    """Deletes a deleted document's stored bytes and rendition, unless the same file is stored
    again. It holds the lock ``store_file`` takes, so an upload of these bytes either finished
    first and is kept, or waits and stores them again."""
    async with open_session() as db:
        await repository.lock_content(db, company_id, sha256)
        if await repository.get_by_hash(db, company_id, sha256) is None:
            await storage.delete(storage_key)
            await storage.delete(rendition_key(storage_key))
        await db.commit()


def document_types() -> list[DocumentTypeOut]:
    return [
        DocumentTypeOut(
            key=t.key,
            label=t.label,
            pack=t.pack,
            proof_of_delivery=t.proof_of_delivery,
            fields=t.schema.model_json_schema(),
        )
        for t in registry.all_types()
    ]


# Reading what a file says


async def request_reading(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> None:
    """Asks for these files to be read: an attachment a person would open, an upload. One asked for
    before is left as it is. Runs in the caller's transaction."""
    await repository.request_reading(db, company_id, document_ids)


async def read_pending() -> int:
    """Reads the files that wait, oldest first, each in a transaction of its own and each in a
    locked child process. A failure outside the file is counted against it, and it is tried again
    next run, up to READ_TRIES times, then once an hour. Returns how many were read."""
    retry_before = utcnow() - READ_RETRY_AFTER
    async with open_session() as db:
        company_ids = await repository.companies_with_pending(
            db, tries=READ_TRIES, retry_before=retry_before
        )
    read = 0
    for company_id in company_ids:
        async with open_session() as db:
            pending = await repository.pending(
                db, company_id, tries=READ_TRIES, retry_before=retry_before, limit=READ_BATCH
            )
        for document_id in pending:
            try:
                async with open_session() as db:
                    await read_document(db, company_id, document_id)
                    await events.publish(db, company_id, events.EventKind.DOCUMENTS)
                    await db.commit()
            except Exception as error:  # one file that fails never stops the others
                # The error's type only: its text can carry what the file holds.
                logger.warning("reading document %s failed: %s", document_id, type(error).__name__)
                async with open_session() as db:
                    await repository.add_read_failure(db, company_id, document_id, utcnow())
                    await db.commit()
                continue
            read += 1
    return read


async def read_document(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Reads one file by code: a PDF's text layer, a spreadsheet's visible cells, the PDF the
    converter makes of a Word file, the text of a web page, XML or text file, and the facts in
    them. A photo, or a PDF with a scanned page, is left for the vision model. Runs in the caller's
    transaction and does not commit."""
    # Shared, so a delete waits for the reading: it stores a rendition, and an archive's files.
    document = await repository.lock_document(db, company_id, document_id, share=True)
    if document is None or document.read_state != ReadState.PENDING or document.needs_vision:
        return
    media = filetypes.media_type(document.content_type, document.filename)
    data = await storage.get(document.storage_key)
    # First: a zip whose first member is a PDF has the PDF's header in its first kilobyte.
    if filetypes.archive_type(document.content_type, document.filename) is not None:
        await _unpack(db, document, data)
        return
    real = filetypes.sniff(data)
    if real == filetypes.PDF:
        read = await _read_pdf(data)
    elif real in filetypes.IMAGES:
        if _holds_writing(data):
            document.needs_vision = True
        else:
            # Nothing was learned of it, so what rests on a file's reading is not told; its name
            # may still say what it is.
            document.read_state, document.read_at = ReadState.READ, utcnow()
            await _classify(db, document)
        return
    elif media in filetypes.SHEETS:
        read = await _read_sheet(data, media)
    elif media in filetypes.OFFICE:
        read = await _read_office(document, data, media)
    elif media in filetypes.TEXTS:
        read = await _read_text(document, data, media)
    else:
        read = isolated.Reason.UNSUPPORTED
    if isinstance(read, isolated.Reason):
        _unreadable(document, read)
        await _became_known(db, company_id, document.id)
        return
    document.text = read.text[:MAX_TEXT]
    document.truncated = read.cut or len(read.text) > MAX_TEXT
    document.parts, document.hidden_parts = read.parts, read.hidden
    document.needs_vision = read.scanned
    await facts.record_facts(
        db,
        company_id=company_id,
        item_kind=ItemKind.DOCUMENT,
        item_id=document.id,
        facts=extractors.from_text(document.text),
    )
    if not read.scanned:
        await _was_read(db, document)


# Typing what code could not


async def type_pending() -> int:
    """Has the typist type the read files code could not, newest first, within each company's
    budget for a day (TYPED_FILES_PER_DAY; at zero no file's text leaves for the model's provider
    for it). Returns how many files were asked about."""
    budget = get_settings().typed_files_per_day
    if budget == 0:
        return 0
    async with open_session() as db:
        company_ids = await repository.companies_with_untyped(db)
    asked = 0
    for company_id in company_ids:
        async with open_session() as db:
            spent = await repository.typed_since(db, company_id, utcnow() - TYPING_WINDOW)
        asked += len(await type_files(company_id, limit=min(TYPING_BATCH, budget - spent)))
    return asked


async def type_files(
    company_id: uuid.UUID, *, limit: int, write: bool = True, usage: RunUsage | None = None
) -> list[tuple[str, str | None]]:
    """Asks the typist about up to ``limit`` of the company's read files that wait for it, newest
    first, and returns each file's name with its answer. A file is asked about once, whatever
    the answer; a type the owner or code gave it meanwhile stands. ``write`` false asks and
    writes nothing (a measured trial). A failure outside the answer (the provider, the network)
    leaves the file and the rest for the next run."""
    if limit <= 0:
        return []
    async with open_session() as db:
        waiting = await repository.untyped(db, company_id, chars=agents.TYPIST_TEXT, limit=limit)
        company = (await companies.get_companies(db, [company_id])).get(company_id)
    trace_content = company is not None and companies.traces_content(company.kind)
    offered = [(each.key, each.label, each.hint) for each in registry.all_types()]
    answered: list[tuple[str, str | None]] = []
    for file in waiting:
        # Taken before the model is asked, so a second run never sends it again.
        if write and not await _reserve(company_id, file.id):
            continue
        try:
            key = await _typed(file, offered, trace_content=trace_content, usage=usage)
        except Exception as error:
            # The error's type only: its text can carry what the file holds.
            logger.warning("typing document %s failed: %s", file.id, type(error).__name__)
            if write:
                async with open_session() as db:
                    await repository.release_typing(db, company_id, file.id)
                    await db.commit()
            break
        answered.append((file.filename, key))
        if not write or key is None:
            continue
        async with open_session() as db:
            if await repository.type_if_untyped(db, company_id, file.id, key, TypeSource.MODEL):
                await _became_known(db, company_id, file.id)
                await events.publish(db, company_id, events.EventKind.DOCUMENTS)
            await db.commit()
    return answered


async def _reserve(company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    """Marks a file as asked about, if no run has taken it: what the day's budget counts."""
    async with open_session() as db:
        taken = await repository.reserve_typing(db, company_id, document_id, utcnow())
        await db.commit()
    return taken


async def _typed(
    file: repository.Untyped,
    offered: Sequence[tuple[str, str, str]],
    *,
    trace_content: bool,
    usage: RunUsage | None,
) -> str | None:
    """The typist's answer for one file: a type it was offered, or None, also when its answer
    could not be used. What the run used is added to ``usage``."""
    # Its own count: the limits hold for one file, not for every file a caller adds up.
    spent = RunUsage()
    try:
        run = await llm.run(
            agents.typist,
            agents.typed(offered, file.filename, file.description, file.text),
            deps=frozenset(key for key, _, _ in offered),
            limits=_TYPING_LIMITS,
            language=None,
            trace_content=trace_content,
            usage=spent,
        )
    except llm.NoAnswer as error:
        logger.warning("the typist gave no type for %s: %s", file.id, error)
        return None
    finally:
        if usage is not None:
            usage.incr(spent)
    return run.output.type


async def holding_nothing(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these files hold nothing of a job, so no mail brings them into a project: one
    typed a signature or logo, and a picture not read yet or read with nothing on it that a model
    could read or say it shows (an icon, a logo, a line of a signature), unless someone typed it.
    A picture that could not be read stays: what it holds is not known."""
    return {
        reading.id
        for reading in await repository.readings(db, company_id, ids)
        if reading.doc_type == registry.SIGNATURE
        or (
            # A type someone gave says what it holds.
            reading.doc_type is None
            and filetypes.media_type(reading.content_type, reading.filename) in filetypes.IMAGES
            and (
                reading.read_state in (None, ReadState.PENDING)
                or (reading.read_state == ReadState.READ and reading.blank)
            )
        )
    }


# Reading what only a vision model can


async def read_pending_with_vision() -> int:
    """Has the vision model read the photos and scanned pages that wait, oldest first, within each
    company's budget of pictures for a day (VISION_PAGES_PER_DAY; at zero nothing is read and no
    file leaves for the model's provider). A file is read whole or not yet: when the oldest one
    needs more pictures than the budget has left, the company's files wait until earlier readings
    are a day old. Only a request made again for one file can pass the budget, by that file's
    pictures. A failure outside the file counts as in ``read_pending``. Returns how many files
    were read."""
    budget = get_settings().vision_pages_per_day
    if budget == 0:
        return 0
    now = utcnow()
    retry_before = now - READ_RETRY_AFTER
    async with open_session() as db:
        company_ids = await repository.companies_with_pending(
            db, tries=READ_TRIES, retry_before=retry_before, vision=True
        )
    read = 0
    for company_id in company_ids:
        async with open_session() as db:
            spent = await repository.vision_pages_since(db, company_id, now - VISION_WINDOW)
            pending = await repository.pending(
                db,
                company_id,
                tries=READ_TRIES,
                retry_before=retry_before,
                limit=VISION_BATCH,
                vision=True,
            )
            company = (await companies.get_companies(db, [company_id])).get(company_id)
        left = budget - spent
        trace_content = company is not None and companies.traces_content(company.kind)
        language = company.language if company is not None else Language.EN
        for document_id in pending:
            try:
                pictures = await read_with_vision(
                    company_id,
                    document_id,
                    left=left,
                    language=language,
                    trace_content=trace_content,
                )
            except Exception as error:  # one file that fails never stops the others
                # The error's type only: its text can carry what the file holds.
                logger.warning(
                    "the vision model reading document %s failed: %s",
                    document_id,
                    type(error).__name__,
                )
                async with open_session() as db:
                    await repository.add_read_failure(db, company_id, document_id, utcnow())
                    await db.commit()
                continue
            if pictures is None:
                break
            await events.publish_now(company_id, [events.EventKind.DOCUMENTS])
            left -= pictures
            read += 1
    return read


async def read_with_vision(
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    left: int,
    language: Language,
    trace_content: bool,
) -> int | None:
    """Has the vision model read one file: a photo as it is, a PDF's scanned pages as pictures
    drawn in the locked child, in a call with no tools (ADR-0013). Returns the pictures the model
    was shown, counted once per request and recorded against the company's budget
    (``vision_reads``), or None when the file needs more than ``left``.

    No transaction is open while the pages are drawn and the model reads: that takes a minute or
    more. The file is locked again to write, and left alone if it changed or went meanwhile."""
    async with open_session() as db:
        found = await repository.get_many(db, company_id, [document_id])
        if not found or not _awaits_vision(found[0]):
            return 0
        filename, text = found[0].filename, found[0].text
        key = found[0].rendition_key or found[0].storage_key
    data = await storage.get(key)
    is_pdf = filetypes.sniff(data) == filetypes.PDF
    scanned = _scanned_pages(text) if is_pdf else []
    shown = scanned[: reading.MAX_PICTURED]
    if (len(shown) if is_pdf else 1) > left:
        return None
    pictures: list[tuple[bytes, str]] | isolated.Reason
    if is_pdf:
        pictures = await _drawn(data, shown)
    else:
        pictures = _photo(data) if _holds_writing(data) else []
    seen: agents.Reading | isolated.Reason
    spent = 0
    if isinstance(pictures, isolated.Reason):
        seen = pictures
    elif not pictures:
        seen = agents.Reading(shows="", pages=[])
    else:
        shown_to = agents.pictures(filename, pictures)
        usage = RunUsage()

        async def ask(*, second: bool = False) -> agents.Reading:
            run = await llm.run(
                agents.reader,
                shown_to,
                deps=len(pictures),
                limits=_VISION_LIMITS,
                language=language,
                trace_content=trace_content,
                usage=usage,
                second=second,
            )
            return run.output

        try:
            seen = await ask()
        except llm.NoAnswer as error:
            logger.warning("the vision model gave no reading of %s: %s", document_id, error)
            seen = isolated.Reason.UNSUPPORTED
        # Every request shows the pictures again and is paid for, whatever came of its answer.
        spent = len(pictures) * max(usage.requests, 1)
        if (
            isinstance(seen, agents.Reading)
            and seen.hard()
            and llm.second_model_name(agents.READER) is not None
            and spent + len(pictures) <= left
        ):
            # What the first model could not read with certainty, the stronger one reads again;
            # when that fails, for any reason, the first reading stands.
            asked = usage.requests
            sent = 1
            try:
                seen = await ask(second=True)
            except Exception as error:
                logger.warning(
                    "the second reading of %s failed: %s", document_id, type(error).__name__
                )
                # A model that settings cannot reach was never sent the pictures.
                sent = 0 if isinstance(error, llm.ModelUnavailable) else 1
            spent += len(pictures) * max(usage.requests - asked, sent)
    async with open_session() as db:
        if spent:
            await repository.add_vision_read(db, company_id, spent, utcnow())
        document = await repository.lock_document(db, company_id, document_id, share=True)
        if document is not None and _awaits_vision(document) and document.text == text:
            document.vision_pages = len(pictures) if spent else 0
            if isinstance(seen, isolated.Reason) and is_pdf:
                # What code read of it stays, and says its scanned pages are missing.
                document.read_state, document.read_at = ReadState.READ, utcnow()
                document.truncated = True
                await _classify(db, document)
            elif isinstance(seen, isolated.Reason):
                _unreadable(document, seen)
                await _became_known(db, company_id, document_id)
            else:
                await _keep_reading(
                    db, document, seen, shown if is_pdf else None, more=len(scanned) > len(shown)
                )
        await db.commit()
    return spent


async def _keep_reading(
    db: AsyncSession,
    document: Document,
    seen: agents.Reading,
    shown: Sequence[int] | None,
    *,
    more: bool,
) -> None:
    """Writes what the vision model read onto the file: a photo's words as its text, a PDF's
    scanned pages (``shown``) into their places among the pages code read. The facts found in what
    the model wrote say a model read them, and are never surer than ``_MODEL_CONFIDENCE``: where
    code found the same fact in the file's own text, code's stays (facts, ``upsert``)."""
    written = [reading.plain(page.text) for page in seen.pages]
    if shown is None:
        text = "\n".join(written)
    else:
        pages = document.text.split("\f")
        for index, page in zip(shown, written, strict=True):
            pages[index] = page
        text = "\f".join(pages)
    document.text = text[:MAX_TEXT]
    document.truncated = document.truncated or more or len(text) > MAX_TEXT
    document.description = " ".join(reading.plain(seen.shows).split())[:300]
    await facts.record_facts(
        db,
        company_id=document.company_id,
        item_kind=ItemKind.DOCUMENT,
        item_id=document.id,
        facts=[
            dataclasses.replace(
                fact,
                extractor=agents.MODEL_READ,
                confidence=min(fact.confidence, _MODEL_CONFIDENCE),
            )
            for fact in extractors.from_text("\n".join(written))
        ],
    )
    await _was_read(db, document)


def _holds_writing(data: bytes) -> bool:
    """Whether a picture is large enough to be worth a model's reading. One whose header gives
    no size is taken as large: the model, not a guess, says what is on it."""
    size = filetypes.picture_size(data)
    if size is None:
        return True
    width, height = size
    return min(width, height) >= _PICTURE_SIDE and width * height >= _PICTURE_PIXELS


def _awaits_vision(document: Document) -> bool:
    return document.read_state == ReadState.PENDING and document.needs_vision


def _scanned_pages(text: str) -> list[int]:
    """The pages of a PDF's text with too little on them to be anything but a scan."""
    return [
        index for index, page in enumerate(text.split("\f")) if len(page.strip()) < _SCANNED_PAGE
    ]


async def _drawn(data: bytes, pages: Sequence[int]) -> list[tuple[bytes, str]] | isolated.Reason:
    """These pages of a PDF as the PNGs the locked child draws of them, each with its type."""
    if not pages:
        return []
    hint = reading.PagesHint(pages=list(pages)).model_dump_json()
    drawn = _checked(reading.PageImages, await isolated.run("documents.page_images", data, hint))
    if isinstance(drawn, isolated.Reason):
        return drawn
    try:
        images = [base64.b64decode(image, validate=True) for image in drawn.images]
    except ValueError:
        return isolated.Reason.DAMAGED
    # A child a crafted file took over can send anything: only PNGs, one per page, go on.
    if len(images) != len(pages) or any(filetypes.sniff(i) != filetypes.PNG for i in images):
        return isolated.Reason.DAMAGED
    return [(image, filetypes.PNG) for image in images]


def _photo(data: bytes) -> list[tuple[bytes, str]] | isolated.Reason:
    """A photo as the vision model is shown it: its own bytes, under the type they prove."""
    real = filetypes.sniff(data)
    if real not in _SHOWN_AS_IT_IS:
        return isolated.Reason.UNSUPPORTED
    if len(data) > MAX_PICTURE:
        return isolated.Reason.TOO_LARGE
    return [(data, real)]


class ConverterMissing(RuntimeError):
    """An office file is due to be read, and CONVERTER_URL is not set."""


@dataclass(frozen=True, slots=True)
class _Read:
    text: str
    # Pages or sheets; None for a file that has neither.
    parts: int | None
    hidden: int
    # The text stops short of the file.
    cut: bool = False
    # A page with too little text on it: the vision model reads it.
    scanned: bool = False


async def _read_pdf(data: bytes) -> _Read | isolated.Reason:
    pdf = _checked(reading.PdfText, await isolated.run("documents.pdf_text", data, filetypes.PDF))
    if isinstance(pdf, isolated.Reason):
        return pdf
    return _Read(
        text="\f".join(pdf.pages),
        parts=pdf.page_count,
        hidden=pdf.hidden,
        cut=pdf.page_count > len(pdf.pages),
        scanned=any(len(page.strip()) < _SCANNED_PAGE for page in pdf.pages),
    )


async def _read_sheet(data: bytes, media: str) -> _Read | isolated.Reason:
    sheet = _checked(reading.SheetText, await isolated.run("documents.sheet_text", data, media))
    if isinstance(sheet, isolated.Reason):
        return sheet
    return _Read(text=sheet.text, parts=sheet.sheet_count, hidden=sheet.hidden, cut=sheet.truncated)


async def _read_office(document: Document, data: bytes, media: str) -> _Read | isolated.Reason:
    """A Word, RTF or OpenDocument file, read from the PDF the converter makes of it, which is
    kept and shown in its place (ADR-0012). Word text marked hidden is not in that PDF."""
    extension = filetypes.office_extension(media, data)
    if extension is None:
        return isolated.Reason.DAMAGED
    if not converter.configured():
        # Not the file's fault: it waits, tried each hour, until CONVERTER_URL is set.
        raise ConverterMissing
    try:
        pdf = await converter.to_pdf(data, extension)
    except converter.Unconvertible as refused:
        # LibreOffice fails short of memory too: the file is damaged only once it failed each try.
        if refused.retry and document.read_failures + 1 < READ_TRIES:
            raise
        return refused.reason
    key = rendition_key(document.storage_key)
    await storage.put(key, pdf, content_type=filetypes.PDF)
    document.rendition_key = key
    return await _read_pdf(pdf)


async def _read_text(document: Document, data: bytes, media: str) -> _Read | isolated.Reason:
    """A web page, XML or text file, read in the locked child (reading.file_text)."""
    if len(data) > MAX_TEXT_FILE:
        return isolated.Reason.TOO_LARGE
    hint = reading.TextHint(media=media, charset=filetypes.charset(document.content_type))
    read = _checked(
        reading.FileText, await isolated.run("documents.file_text", data, hint.model_dump_json())
    )
    if isinstance(read, isolated.Reason):
        return read
    return _Read(text=read.text, parts=None, hidden=read.hidden)


def rendition_key(storage_key: str) -> str:
    return f"{storage_key}.pdf"


class _Refused(Exception):
    def __init__(self, reason: isolated.Reason) -> None:
        super().__init__(reason.value)
        self.reason = reason


async def _unpack(db: AsyncSession, document: Document, data: bytes) -> None:
    """Takes the files out of an archive or a winmail.dat in the locked child, a batch per run,
    and stores each as a document of its own, joined to the archive under the name it had there.
    Each is then read like an attachment, except an archive inside an archive inside another:
    one level of nesting is opened, nothing deeper. A refused run leaves nothing of the archive
    behind but its reason."""
    kind = filetypes.archive_kind(document.content_type, document.filename, data)
    if kind is None:
        _unreadable(document, isolated.Reason.DAMAGED)
        return
    company_id = document.company_id
    # What went to storage, by hash and key: the rows themselves go with a refused savepoint.
    stored: list[tuple[bytes, str]] = []
    try:
        async with db.begin_nested():
            count, omitted = await _take_out(db, document, unpacking.Kind(kind), data, stored)
    except _Refused as refused:
        _unreadable(document, refused.reason)
        # The rows went with the savepoint, the bytes already in storage did not: each goes
        # unless a file stored before holds it.
        from app.modules.documents import tasks

        for sha256, storage_key in stored:
            await jobs.defer(
                db,
                tasks.forget_file,
                args={
                    "company_id": str(company_id),
                    "sha256": sha256.hex(),
                    "storage_key": storage_key,
                },
            )
        return
    document.parts, document.truncated = count, omitted > 0
    document.read_state, document.read_at = ReadState.READ, utcnow()
    for listener in _unpack_listeners:
        await listener(db, company_id, document.id)


async def _take_out(
    db: AsyncSession,
    document: Document,
    kind: unpacking.Kind,
    data: bytes,
    stored: list[tuple[bytes, str]],
) -> tuple[int, int]:
    """The runs of the child, each reply held to the archive's limits by what this process
    counts itself (``stored`` gathers what went to storage): a child a crafted file took over
    can report anything."""
    company_id = document.company_id
    codepage = await _codepage(db, company_id)
    nested = await repository.is_part(db, company_id, document.id)
    request = unpacking.Request(kind=kind, codepage=codepage)
    position = taken = 0
    while True:
        outcome = await isolated.run(
            "documents.unpack", data, request.model_dump_json(), limits=_UNPACK_LIMITS
        )
        batch = _checked(unpacking.Unpacked, outcome)
        if isinstance(batch, isolated.Reason):
            raise _Refused(batch)
        # Each run but the last sends at least one member, and no more than the cap in all, so
        # the runs end.
        if position + len(batch.members) > unpacking.MAX_MEMBERS or (
            batch.next is not None and not batch.members
        ):
            raise _Refused(isolated.Reason.DAMAGED)
        parts: list[DocumentPart] = []
        to_read: list[uuid.UUID] = []
        for member in batch.members:
            part = DocumentPart(
                company_id=company_id,
                container_id=document.id,
                position=position,
                name=member.name,
                size=member.size,
            )
            position += 1
            parts.append(part)
            if member.data is None:
                part.unreadable_reason = member.refused or isolated.Reason.PASSWORD
                continue
            try:
                content = base64.b64decode(member.data, validate=True)
            except ValueError as error:
                raise _Refused(isolated.Reason.DAMAGED) from error
            taken += len(content)
            if unpacking.too_much(taken, len(data)):
                raise _Refused(isolated.Reason.TOO_LARGE)
            name = PurePosixPath(member.name).name or member.name
            file = await store_file(
                db,
                company_id=company_id,
                data=content,
                filename=name,
                content_type=_guessed_type(name, content),
            )
            part.document_id, part.size = file.id, len(content)
            stored.append((file.sha256, file.storage_key))
            if not (nested and filetypes.archive_type(file.content_type, file.filename)):
                to_read.append(file.id)
        await repository.add_parts(db, parts)
        await request_reading(db, company_id, to_read)
        if batch.next is None:
            return position, batch.omitted
        # Each run sends at least one member, so a run that did not move on is a broken reply.
        if batch.next <= request.start:
            raise _Refused(isolated.Reason.DAMAGED)
        spent = max(batch.spent, taken)
        request = request.model_copy(update={"start": batch.next, "spent": spent})


def _guessed_type(name: str, content: bytes) -> str:
    """A member has no declared type, so its name says what to expect, as it does for a file a
    mail client labels ``application/octet-stream``. A name that says nothing (a picture Outlook
    put in a ``winmail.dat`` with no extension) leaves it to the image or PDF its bytes prove."""
    return (
        filetypes.archive_type("", name)
        or filetypes.media_type("", name)
        or filetypes.sniff(content)
        or "application/octet-stream"
    )


async def _codepage(db: AsyncSession, company_id: uuid.UUID) -> str:
    company = (await companies.get_companies(db, [company_id])).get(company_id)
    country = company.country if company is not None else ""
    return _codepages.get(country, unpacking.DEFAULT_CODEPAGE)


def _checked[T: BaseModel](shape: type[T], outcome: isolated.Outcome) -> T | isolated.Reason:
    """What the child sent, checked like any input from outside, or why the file is unreadable."""
    if outcome.refused is not None:
        return outcome.refused
    try:
        return shape.model_validate(outcome.value)
    except ValidationError:
        return isolated.Reason.DAMAGED


def _unreadable(document: Document, reason: isolated.Reason) -> None:
    document.read_state = ReadState.UNREADABLE
    document.unreadable_reason = reason
    document.read_at = utcnow()


async def document_exists(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    """Whether it exists, held so until the caller's transaction ends: a link made to it cannot
    name a file a concurrent delete removes."""
    return await repository.lock_document(db, company_id, document_id, share=True) is not None


async def containers_of(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID
) -> list[uuid.UUID]:
    """The archives a file was taken out of, the archive around one of them too."""
    found: list[uuid.UUID] = []
    waiting = [document_id]
    while waiting:
        outer = await repository.containers_of(db, company_id, waiting)
        waiting = [each for each in outer if each not in found and each != document_id]
        found.extend(waiting)
    return found


async def containers_of_each(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """``containers_of`` for several files, an archive's depth at a time in one query."""
    found: dict[uuid.UUID, list[uuid.UUID]] = {each: [] for each in document_ids}
    # Which of the files asked about each archive at this depth holds.
    reaching: dict[uuid.UUID, set[uuid.UUID]] = {each: {each} for each in document_ids}
    while reaching:
        deeper: dict[uuid.UUID, set[uuid.UUID]] = {}
        for member, container in await repository.container_pairs(db, company_id, list(reaching)):
            for origin in reaching[member]:
                if container != origin and container not in found[origin]:
                    found[origin].append(container)
                    deeper.setdefault(container, set()).add(origin)
        reaching = deeper
    return found


async def members_of(
    db: AsyncSession, company_id: uuid.UUID, container_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """What each of these archives held, an archive inside one included: the files that could be
    taken out, by container."""
    members: dict[uuid.UUID, list[uuid.UUID]] = {}
    asked = set(container_ids)
    found = await repository.members_of(db, company_id, container_ids)
    while found:
        members |= found
        nested = {part for parts in found.values() for part in parts} - asked
        asked |= nested
        found = await repository.members_of(db, company_id, sorted(nested))
    return members


async def for_the_vision_model(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these files the vision model reads or has read: a photo large enough to hold
    writing, a PDF with a scanned page."""
    return await repository.for_the_vision_model(db, company_id, ids)


async def get_documents(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Document]:
    return {row.id: row for row in await repository.get_many(db, company_id, ids)}


async def document_outs(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, DocumentOut]:
    """These files as a list shows them; one that is gone is left out."""
    if not ids:
        return {}
    return {row.id: _out(row) for row in await repository.get_many(db, company_id, ids)}


async def _document(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    found = await repository.get_many(db, company_id, [document_id])
    if not found:
        raise NotFound("No such document")
    return found[0]


def _check_type(doc_type: str | None) -> None:
    if doc_type is not None and registry.get(doc_type) is None:
        raise BadRequest(f"Unknown document type {doc_type!r}", field="doc_type")


async def _read_limited(chunks: AsyncIterator[bytes], limit: int) -> bytes:
    data = bytearray()
    async for chunk in chunks:
        data += chunk
        if len(data) > limit:
            raise PayloadTooLarge(f"The file is larger than {limit // (1024 * 1024)} MB")
    return bytes(data)


def _out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        size=document.size,
        doc_type=document.doc_type,
        doc_type_source=document.doc_type_source,
        sha256=document.sha256.hex(),
        created_at=document.created_at,
        preview=filetypes.preview(document.content_type, document.filename),
        read_state=document.read_state,
        unreadable_reason=document.unreadable_reason,
        truncated=document.truncated,
        converted=document.rendition_key is not None,
        description=document.description,
    )
