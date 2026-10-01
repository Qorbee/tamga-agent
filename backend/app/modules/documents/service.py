"""Documents: files stored once per company by content (ADR-0001 storage, private), what kind of
document each is (the registry), the files people upload by hand, and what each file says, read in
a locked child process (ADR-0012) without what its viewer hides (ADR-0013)."""

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
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
    UploadOut,
    WorkbookOut,
)
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.platform import filetypes, isolated, storage
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, NotFound, PayloadTooLarge
from app.platform.items import ItemKind
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

_UPLOADERS: Final = (Role.OWNER, Role.OPS)
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


async def store_file(
    db: AsyncSession, *, company_id: uuid.UUID, data: bytes, filename: str, content_type: str
) -> Document:
    """Writes the bytes to storage and returns the company's document for them. Runs inside the
    caller's transaction and does not commit. If that transaction rolls back, the object stays in
    storage under its content key, where the next attempt finds it again."""
    sha256 = hashlib.sha256(data).digest()
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
    )


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


async def set_type(
    db: AsyncSession, actor: Actor, document_id: uuid.UUID, doc_type: str | None
) -> DocumentOut:
    actor.require(*_UPLOADERS)
    _check_type(doc_type)
    document = await _document(db, actor.company_id, document_id)
    document.doc_type = doc_type
    await db.commit()
    return _out(document)


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
    return bool(await repository.get_many(db, company_id, [document_id]))


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
