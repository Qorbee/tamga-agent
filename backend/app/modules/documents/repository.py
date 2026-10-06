import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import (
    Document,
    DocumentPart,
    ReadState,
    TypeSource,
    Upload,
    VisionRead,
)
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


async def filenames(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(Document.id, Document.filename).where(
            Document.company_id == company_id, Document.id.in_(ids)
        )
    )
    return {document_id: filename for document_id, filename in rows.tuples().all()}


async def type_if_untyped(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID, key: str, source: TypeSource
) -> bool:
    """Types a file only if its stored row has no type, in one statement."""
    typed = await db.execute(
        update(Document)
        .where(
            Document.company_id == company_id,
            Document.id == document_id,
            Document.doc_type.is_(None),
        )
        .values(doc_type=key, doc_type_source=source)
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(typed, "rowcount", 0))


@dataclass(frozen=True, slots=True)
class Reading:
    """What is known of a file's reading, without its text."""

    id: uuid.UUID
    filename: str
    content_type: str
    doc_type: str | None
    read_state: ReadState | None
    # Neither code nor a model found anything written on it, nor a model anything it shows.
    blank: bool


async def readings(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[Reading]:
    if not ids:
        return []
    rows = await db.execute(
        select(
            Document.id,
            Document.filename,
            Document.content_type,
            Document.doc_type,
            Document.read_state,
            (Document.text == "") & (Document.description == ""),
        ).where(Document.company_id == company_id, Document.id.in_(ids))
    )
    return [Reading(*row) for row in rows.tuples()]


def _untyped() -> ColumnElement[bool]:
    """Read, with something to tell its type by, untyped, and the typist not asked yet."""
    return (
        (Document.read_state == ReadState.READ)
        & Document.doc_type.is_(None)
        & Document.typed_at.is_(None)
        & ((Document.text != "") | (Document.description != ""))
    )


async def companies_with_untyped(db: AsyncSession) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone: each company's files are then typed
    under its own."""
    rows = await db.scalars(select(Document.company_id).where(_untyped()).distinct())
    return list(rows)


@dataclass(frozen=True, slots=True)
class Untyped:
    id: uuid.UUID
    filename: str
    description: str
    # As much of its text as the typist is shown.
    text: str


async def untyped(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    chars: int,
    limit: int,
    only: Sequence[uuid.UUID] | None = None,
) -> list[Untyped]:
    """Files waiting for the typist, newest first: what the owner looks at now comes first.
    ``only`` keeps those of these files."""
    filters = [Document.company_id == company_id, _untyped()]
    if only is not None:
        filters.append(Document.id.in_(only))
    rows = await db.execute(
        select(
            Document.id, Document.filename, Document.description, func.left(Document.text, chars)
        )
        .where(*filters)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(limit)
    )
    return [Untyped(*row) for row in rows.tuples()]


async def answered_none(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID], *, limit: int | None
) -> list[uuid.UUID]:
    """Which of these files the typist was asked about and gave no type, newest first."""
    rows = await db.scalars(
        select(Document.id)
        .where(
            Document.company_id == company_id,
            Document.id.in_(ids),
            Document.doc_type.is_(None),
            Document.doc_type_source.is_(None),
            Document.typed_at.is_not(None),
        )
        .order_by(Document.created_at.desc(), Document.id)
        .limit(limit)
    )
    return list(rows)


async def ask_again(db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> int:
    """Lets the typist be asked again about these files, where none has a type yet."""
    asked = await db.execute(
        update(Document)
        .where(
            Document.company_id == company_id,
            Document.id.in_(ids),
            Document.doc_type.is_(None),
            Document.doc_type_source.is_(None),
        )
        .values(typed_at=None)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(asked, "rowcount", 0))


async def typed_since(db: AsyncSession, company_id: uuid.UUID, since: datetime) -> int:
    """Files the typist was asked about for this company since then: what its budget counts."""
    count = await db.scalar(
        select(func.count()).where(Document.company_id == company_id, Document.typed_at >= since)
    )
    return int(count or 0)


async def reserve_typing(
    db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID, at: datetime
) -> bool:
    """Marks a file as asked about, in one statement, unless a run already took it or it was
    typed meanwhile."""
    taken = await db.execute(
        update(Document)
        .where(
            Document.company_id == company_id,
            Document.id == document_id,
            Document.typed_at.is_(None),
            Document.doc_type.is_(None),
        )
        .values(typed_at=at)
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(taken, "rowcount", 0))


async def release_typing(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """Gives a file back to the next run: its model was not reached."""
    await db.execute(
        update(Document)
        .where(Document.company_id == company_id, Document.id == document_id)
        .values(typed_at=None)
        .execution_options(synchronize_session=False)
    )


async def untyped_read(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Document]:
    """The company's files that were read and have no type yet."""
    rows = await db.scalars(
        select(Document)
        .where(
            Document.company_id == company_id,
            Document.read_state == ReadState.READ,
            Document.doc_type.is_(None),
        )
        .order_by(Document.id)
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


async def read_after(
    db: AsyncSession, company_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> Sequence[Document]:
    """The read files after ``after``, by id."""
    filters = [Document.company_id == company_id, Document.read_state == ReadState.READ]
    if after is not None:
        filters.append(Document.id > after)
    rows = await db.scalars(select(Document).where(*filters).order_by(Document.id).limit(limit))
    return rows.all()


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


async def for_the_vision_model(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    if not ids:
        return set()
    rows = await db.scalars(
        select(Document.id).where(
            Document.company_id == company_id,
            Document.id.in_(ids),
            Document.needs_vision.is_(True),
        )
    )
    return set(rows)


def _waiting(tries: int, retry_before: datetime, *, vision: bool) -> ColumnElement[bool]:
    """Waiting to be read, by code or (``vision``) by the vision model code left it for, and not
    failed too often lately (one that failed ``tries`` times waits until its last failure is older
    than ``retry_before``)."""
    return (
        (Document.read_state == ReadState.PENDING)
        & Document.needs_vision.is_(vision)
        & ((Document.read_failures < tries) | (Document.read_failed_at <= retry_before))
    )


async def companies_with_pending(
    db: AsyncSession, *, tries: int, retry_before: datetime, vision: bool = False
) -> list[uuid.UUID]:
    """The one read across companies, of their ids alone: each company's files are then read under
    its own."""
    rows = await db.scalars(
        select(Document.company_id).where(_waiting(tries, retry_before, vision=vision)).distinct()
    )
    return list(rows)


async def pending(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    tries: int,
    retry_before: datetime,
    limit: int,
    vision: bool = False,
) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Document.id)
        .where(Document.company_id == company_id, _waiting(tries, retry_before, vision=vision))
        .order_by(Document.created_at, Document.id)
        .limit(limit)
    )
    return list(rows)


async def vision_pages_since(db: AsyncSession, company_id: uuid.UUID, since: datetime) -> int:
    """Pictures the vision model was shown for this company since then: what its budget counts."""
    pages = await db.scalar(
        select(func.coalesce(func.sum(VisionRead.pages), 0)).where(
            VisionRead.company_id == company_id, VisionRead.read_at >= since
        )
    )
    return int(pages or 0)


async def add_vision_read(
    db: AsyncSession, company_id: uuid.UUID, pages: int, at: datetime
) -> None:
    db.add(VisionRead(company_id=company_id, pages=pages, read_at=at))
    await db.flush()


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


async def containers_of(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """The archives these files were taken out of."""
    rows = await db.scalars(
        select(DocumentPart.container_id)
        .where(DocumentPart.company_id == company_id, DocumentPart.document_id.in_(document_ids))
        .distinct()
    )
    return list(rows.all())


async def container_pairs(
    db: AsyncSession, company_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Each of these files with each archive it was taken out of."""
    rows = await db.execute(
        select(DocumentPart.document_id, DocumentPart.container_id)
        .where(DocumentPart.company_id == company_id, DocumentPart.document_id.in_(document_ids))
        .distinct()
    )
    return [(member, container) for member, container in rows.tuples() if member is not None]


async def members_of(
    db: AsyncSession, company_id: uuid.UUID, container_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """The files taken out of each of these containers, those that could be."""
    if not container_ids:
        return {}
    rows = await db.execute(
        select(DocumentPart.container_id, DocumentPart.document_id)
        .where(
            DocumentPart.company_id == company_id,
            DocumentPart.container_id.in_(container_ids),
            DocumentPart.document_id.is_not(None),
        )
        .order_by(DocumentPart.container_id, DocumentPart.position)
    )
    members: dict[uuid.UUID, list[uuid.UUID]] = {}
    for container_id, document_id in rows.tuples():
        if document_id is not None:
            members.setdefault(container_id, []).append(document_id)
    return members


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
