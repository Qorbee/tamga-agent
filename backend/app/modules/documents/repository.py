import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import Document
from app.platform.pagination import PageParams, escape_like


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
    page: PageParams,
) -> tuple[Sequence[Document], int]:
    filters: list[ColumnElement[bool]] = [Document.company_id == company_id]
    if q:
        filters.append(Document.filename.ilike(f"%{escape_like(q)}%", escape="\\"))
    if doc_type is not None:
        filters.append(Document.doc_type == doc_type)
    if untyped:
        filters.append(Document.doc_type.is_(None))
    total = await db.scalar(select(func.count()).select_from(Document).where(*filters))
    rows = await db.scalars(
        select(Document)
        .where(*filters)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0
