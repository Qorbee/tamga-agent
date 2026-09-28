"""Documents: files stored once per company, by content (ADR-0001 storage, private)."""

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents import repository
from app.modules.documents.models import Document
from app.platform import storage


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
