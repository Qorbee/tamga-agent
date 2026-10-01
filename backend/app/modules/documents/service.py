"""Documents: files stored once per company by content (ADR-0001 storage, private), what kind of
document each is (the registry), the files people upload by hand, and what each file says, read in
a locked child process (ADR-0012) without what its viewer hides (ADR-0013)."""

import base64
import hashlib
import logging
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Final

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.documents import reading, registry, repository, unpacking
from app.modules.documents.models import Document, DocumentPart, ReadState, Upload
from app.modules.documents.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentPage,
    DocumentPartOut,
    DocumentTypeOut,
    DocumentUpdate,
    Holder,
    UploadOut,
    WorkbookOut,
)
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.platform import filetypes, isolated, jobs, storage
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound, PayloadTooLarge
from app.platform.items import ItemKind
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

# The runs that take files out of an archive read up to 200 MB of it, so each gets two minutes.
_UNPACK_LIMITS: Final = isolated.Limits(deadline_seconds=120.0)

# Whether a module that keeps documents holds this one (a mail's attachments, a project's evidence).
type HoldsCheck = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[bool]]
_holders: dict[Holder, HoldsCheck] = {}
# The DOS code page a country's Windows writes a zip's names in, when not as UTF-8 (ADR-0012).
_codepages: dict[str, str] = {}


def register_holder(holder: Holder, holds: HoldsCheck) -> None:
    """A module that keeps documents says, through ``holds``, whether it keeps one. Only a file no
    holder keeps can be deleted."""
    _holders[holder] = holds


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
    # The uploader's type fills an unknown one; it never replaces a type already recorded.
    if doc_type is not None and document.doc_type is None:
        document.doc_type = doc_type
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


async def update_document(
    db: AsyncSession, actor: Actor, document_id: uuid.UUID, body: DocumentUpdate
) -> DocumentOut:
    actor.require(*_UPLOADERS)
    document = await _document(db, actor.company_id, document_id)
    if "doc_type" in body.model_fields_set:
        _check_type(body.doc_type)
        document.doc_type = body.doc_type
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
    """Deletes a deleted document's stored bytes, unless the same file is stored again. It holds
    the lock ``store_file`` takes, so an upload of these bytes either finished first and is kept,
    or waits and stores them again."""
    async with open_session() as db:
        await repository.lock_content(db, company_id, sha256)
        if await repository.get_by_hash(db, company_id, sha256) is None:
            await storage.delete(storage_key)
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
    """Reads one file by code: a PDF's text layer, a spreadsheet's visible cells, and the facts in
    them. A photo, or a PDF with a scanned page, is left for the vision model. Runs in the caller's
    transaction and does not commit."""
    found = await repository.get_many(db, company_id, [document_id])
    if not found or found[0].read_state != ReadState.PENDING or found[0].needs_vision:
        return
    document = found[0]
    data = await storage.get(document.storage_key)
    # First: a zip whose first member is a PDF has the PDF's header in its first kilobyte.
    if filetypes.archive_type(document.content_type, document.filename) is not None:
        await _unpack(db, document, data)
        return
    real = filetypes.sniff(data)
    media = filetypes.media_type(document.content_type, document.filename)
    if real == filetypes.PDF:
        pdf = _checked(reading.PdfText, await isolated.run("documents.pdf_text", data, real))
        if isinstance(pdf, isolated.Reason):
            _unreadable(document, pdf)
            return
        text, parts, hidden = "\f".join(pdf.pages), pdf.page_count, pdf.hidden
        cut = pdf.page_count > len(pdf.pages)
        scanned = any(len(page.strip()) < _SCANNED_PAGE for page in pdf.pages)
    elif real in filetypes.IMAGES:
        document.needs_vision = True
        return
    elif media in filetypes.SHEETS:
        sheet = _checked(reading.SheetText, await isolated.run("documents.sheet_text", data, media))
        if isinstance(sheet, isolated.Reason):
            _unreadable(document, sheet)
            return
        text, parts, hidden, scanned = sheet.text, sheet.sheet_count, sheet.hidden, False
        cut = sheet.truncated
    else:
        # Word and the rest wait for their readers (ADR-0012), which read them again.
        _unreadable(document, isolated.Reason.UNSUPPORTED)
        return
    document.text = text[:MAX_TEXT]
    document.truncated = cut or len(text) > MAX_TEXT
    document.parts, document.hidden_parts = parts, hidden
    document.needs_vision = scanned
    if not scanned:
        document.read_state, document.read_at = ReadState.READ, utcnow()
    await facts.record_facts(
        db,
        company_id=company_id,
        item_kind=ItemKind.DOCUMENT,
        item_id=document.id,
        facts=extractors.from_text(document.text),
    )


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
                content_type=_guessed_type(name),
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


def _guessed_type(name: str) -> str:
    """A member has no declared type, so its name says what to expect, as it does for a file a
    mail client labels ``application/octet-stream``."""
    return (
        filetypes.archive_type("", name)
        or filetypes.media_type("", name)
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


async def get_documents(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Document]:
    return {row.id: row for row in await repository.get_many(db, company_id, ids)}


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
        sha256=document.sha256.hex(),
        created_at=document.created_at,
        preview=filetypes.preview(document.content_type, document.filename),
        read_state=document.read_state,
        unreadable_reason=document.unreadable_reason,
        truncated=document.truncated,
    )
