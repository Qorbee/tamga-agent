import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import Document, DocumentPart, ReadState, Upload
from app.platform.pagination import PageParams, escape_like


async def lock_content(db: AsyncSession, company_id: uuid.UUID, sha256: bytes) -> None:
    """Until the transaction ends, no one else stores or forgets these bytes for the company: a
    deleted file's object is never removed from under an upload of the same file."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"documents:{company_id}:{sha256.hex()}"},
    )


async def get_by_hash(db: AsyncSession, company_id: uuid.UUID, sha256: bytes) -> Document | None:
    document = await db.scalar(
        select(Document).where(Document.company_id == company_id, Document.sha256 == sha256)
    )
    return document


async def insert_unless_stored(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    sha256: bytes,
    storage_key: str,
    filename: str,
    content_type: str,
    size: int,
) -> Document:
    """The company's document for these bytes: inserted now, or the one already there. The unique
    index decides, so two polls storing the same file at once both get the same row."""
    await db.execute(
        insert(Document)
        .values(
            company_id=company_id,
            sha256=sha256,
            storage_key=storage_key,
            filename=filename,
            content_type=content_type,
            size=size,
        )
        .on_conflict_do_nothing(index_elements=[Document.company_id, Document.sha256])
    )
    document = await db.scalar(
        select(Document).where(Document.company_id == company_id, Document.sha256 == sha256)
    )
    if document is None:  # inserted or already present above, in this transaction
        raise RuntimeError("document vanished between insert and select")
    return document


async def get_many(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Sequence[Document]:
    rows = await db.scalars(
        select(Document).where(Document.company_id == company_id, Document.id.in_(ids))
    )
    return rows.all()


async def list_documents(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    q: str | None,
    doc_type: str | None,
    untyped: bool,
    members: bool,
    page: PageParams,
) -> tuple[Sequence[Document], int]:
    filters: list[ColumnElement[bool]] = [Document.company_id == company_id]
    if q:
        filters.append(Document.filename.ilike(f"%{escape_like(q)}%", escape="\\"))
    if doc_type is not None:
        filters.append(Document.doc_type == doc_type)
    if untyped:
        filters.append(Document.doc_type.is_(None))
    last_upload = (
        select(func.max(Upload.created_at))
        .where(Upload.company_id == company_id, Upload.document_id == Document.id)
        .scalar_subquery()
    )
    if not members:
        # A file taken out of an archive opens from it, unless someone uploaded it too.
        member = (
            select(DocumentPart.id)
            .where(DocumentPart.company_id == company_id, DocumentPart.document_id == Document.id)
            .exists()
        )
        filters.append(or_(~member, last_upload.is_not(None)))
    # Newest first, by when it arrived or was last uploaded: the same file uploaded again comes back
    # to the top. GREATEST passes over a null.
    order = func.greatest(Document.created_at, last_upload).desc()
    total = await db.scalar(select(func.count()).select_from(Document).where(*filters))
    rows = await db.scalars(
        select(Document)
        .where(*filters)
        .order_by(order, Document.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def request_reading(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> None:
    """Marks documents nobody asked to read yet as waiting; one waiting or read is left alone."""
    if not ids:
        return
    await db.execute(
        update(Document)
        .where(
            Document.company_id == company_id,
            Document.id.in_(ids),
            Document.read_state.is_(None),
        )
        .values(read_state=ReadState.PENDING)
    )


def _waiting(tries: int, retry_before: datetime) -> ColumnElement[bool]:
    """Waiting for code to read it: not left for the vision model, and not failed too often lately
    (one that failed ``tries`` times waits until its last failure is older than
    ``retry_before``)."""
    return (
        (Document.read_state == ReadState.PENDING)
        & Document.needs_vision.is_(False)
        & ((Document.read_failures < tries) | (Document.read_failed_at <= retry_before))
    )


async def companies_with_pending(
    db: AsyncSession, *, tries: int, retry_before: datetime
) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone: each company's files are then read under
    its own."""
    rows = await db.scalars(
        select(Document.company_id).where(_waiting(tries, retry_before)).distinct()
    )
    return list(rows)


async def pending(
    db: AsyncSession, company_id: uuid.UUID, *, tries: int, retry_before: datetime, limit: int
) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Document.id)
        .where(Document.company_id == company_id, _waiting(tries, retry_before))
        .order_by(Document.created_at, Document.id)
        .limit(limit)
    )
    return list(rows)


async def add_read_failure(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID, at: datetime
) -> None:
    await db.execute(
        update(Document)
        .where(Document.company_id == company_id, Document.id == document_id)
        .values(read_failures=Document.read_failures + 1, read_failed_at=at)
    )


async def lock_document(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID, *, share: bool = False
) -> Document | None:
    """The document, locked until the transaction ends: for deleting it (exclusive), or, shared,
    for a link to it that must not name a file deleted meanwhile."""
    statement = select(Document).where(
        Document.company_id == company_id, Document.id == document_id
    )
    document: Document | None = await db.scalar(statement.with_for_update(key_share=share))
    return document


async def delete_document(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Deletes the row; its uploads go with it (their foreign key cascades)."""
    await db.execute(
        delete(Document).where(Document.company_id == company_id, Document.id == document_id)
    )


async def add_parts(db: AsyncSession, parts: Sequence[DocumentPart]) -> None:
    db.add_all(parts)
    await db.flush()


async def parts_of(
    db: AsyncSession, company_id: uuid.UUID, container_id: uuid.UUID
) -> Sequence[DocumentPart]:
    rows = await db.scalars(
        select(DocumentPart)
        .where(DocumentPart.company_id == company_id, DocumentPart.container_id == container_id)
        .order_by(DocumentPart.position)
    )
    return rows.all()


async def is_part(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    """Whether the file was taken out of an archive."""
    found = await db.scalar(
        select(DocumentPart.id)
        .where(DocumentPart.company_id == company_id, DocumentPart.document_id == document_id)
        .limit(1)
    )
    return found is not None


async def has_uploads(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(Upload.id)
        .where(Upload.company_id == company_id, Upload.document_id == document_id)
        .limit(1)
    )
    return found is not None
