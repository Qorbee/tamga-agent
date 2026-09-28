"""Documents: files stored once per company by content (ADR-0001 storage, private), what kind of
document each is (the registry), and the files people upload by hand."""

import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.documents import registry, repository
from app.modules.documents.models import Document, Upload
from app.modules.documents.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentPage,
    DocumentTypeOut,
    UploadOut,
)
from app.platform import storage
from app.platform.auth import Actor, Role
from app.platform.errors import BadRequest, NotFound, PayloadTooLarge
from app.platform.pagination import PageParams

_UPLOADERS: Final = (Role.OWNER, Role.OPS)


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
    )
