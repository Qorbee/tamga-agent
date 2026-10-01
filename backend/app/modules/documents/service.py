"""Documents: files stored once per company by content (ADR-0001 storage, private), what kind of
document each is (the registry), the files people upload by hand, and what each file says, read in
a locked child process (ADR-0012) without what its viewer hides (ADR-0013)."""

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
from app.modules.documents import reading, registry, repository
from app.modules.documents.models import Document, ReadState, Upload
from app.modules.documents.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentPage,
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
_NAMING: Final = frozenset({Holder.MAIL})
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

# Whether a module that keeps documents holds this one (a mail's attachments, a project's evidence).
type HoldsCheck = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[bool]]
_holders: dict[Holder, HoldsCheck] = {}


def register_holder(holder: Holder, holds: HoldsCheck) -> None:
    """A module that keeps documents says, through ``holds``, whether it keeps one. Only a file no
    holder keeps can be deleted."""
    _holders[holder] = holds


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
    page: PageParams,
) -> DocumentPage:
    rows, total = await repository.list_documents(
        db,
        actor.company_id,
        q=q.strip() if q else None,
        doc_type=doc_type,
        untyped=untyped,
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
    """A file a mail brought keeps the name it came with: it is the one name for it everywhere,
    and the sender's. Only a file people put in themselves is theirs to name."""
    # A mail storing the same file waits on the content lock and a new link on the row's, so
    # neither lands between the check and the rename.
    await repository.lock_content(db, company_id, document.sha256)
    if await repository.lock_document(db, company_id, document.id) is None:
        raise NotFound("No such document")
    if any(holder in _NAMING for holder in await _held_by(db, company_id, document.id)):
        raise Conflict("A file that came in a mail keeps the name it came with", field="filename")


def _new_name(old: str, new: str) -> str:
    """A name to show the file under. Its extension says what kind of file it is (a file with a
    vague declared type is shown and read by it), so a new name keeps the old one's."""
    name = new.strip()
    if not name or any(unicodedata.category(char) == "Cc" for char in name):
        raise BadRequest("Name the file", field="filename")
    extension = PurePosixPath(old).suffix
    if PurePosixPath(name).suffix.lower() != extension.lower():
        raise BadRequest(f"Keep the file's extension ({extension or 'none'})", field="filename")
    return name


async def delete_document(db: AsyncSession, actor: Actor, document_id: uuid.UUID) -> None:
    """Deletes an uploaded file, with what was read from it. A file a mail brought or a project
    holds as evidence is refused (``held_by``). Its stored bytes go in a job after the commit,
    unless the same file has been stored again by then."""
    actor.require(*_UPLOADERS)
    document = await _document(db, actor.company_id, document_id)
    # A mail storing the same file waits on the content lock and a new link on the row's, so
    # neither lands between the check and the delete: a mail's attachment row would cascade away.
    await repository.lock_content(db, actor.company_id, document.sha256)
    if await repository.lock_document(db, actor.company_id, document_id) is None:
        raise NotFound("No such document")
    if await _held_by(db, actor.company_id, document_id):
        raise Conflict("Only an uploaded file that nothing else keeps can be deleted")
    stored = {
        "company_id": str(actor.company_id),
        "sha256": document.sha256.hex(),
        "storage_key": document.storage_key,
    }
    await facts.forget_item(
        db, company_id=actor.company_id, item_kind=ItemKind.DOCUMENT, item_id=document_id
    )
    await repository.delete_document(db, actor.company_id, document_id)
    # tasks imports this module, so the job is found here, where it is deferred.
    from app.modules.documents import tasks

    await jobs.defer(db, tasks.forget_file, args=stored)
    await db.commit()


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
        # Word, archives and the rest wait for their readers (ADR-0012), which read them again.
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
