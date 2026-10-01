import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import bindparam, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.translations.models import Translation
from app.platform.language import Language


async def lock(db: AsyncSession, company_id: uuid.UUID) -> None:
    """One request at a time reads the company's budget and reserves from it, until it commits."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"tamga.translations.{company_id}"},
    )


async def find(
    db: AsyncSession, company_id: uuid.UUID, language: Language, hashes: Sequence[bytes]
) -> dict[bytes, str]:
    """The translations that were answered, by the hash of their text."""
    rows = await db.execute(
        select(Translation.source_hash, Translation.text).where(
            Translation.company_id == company_id,
            Translation.language == language,
            Translation.source_hash.in_(hashes),
            Translation.text.is_not(None),
        )
    )
    return {source_hash: translated for source_hash, translated in rows.all()}


async def reserve(
    db: AsyncSession,
    company_id: uuid.UUID,
    language: Language,
    chars: Mapping[bytes, int],
    *,
    now: datetime,
    stale_before: datetime,
) -> set[bytes]:
    """Marks each text as asked, counted from ``now``, and returns those this call may send to
    the model: the ones nobody asked about before, and the ones asked before ``stale_before``
    that were never answered. A text another request is asking about right now is not returned."""
    if not chars:
        return set()
    asked = insert(Translation).values(
        [
            {
                "id": uuid.uuid7(),
                "company_id": company_id,
                "source_hash": source_hash,
                "language": language,
                "chars": count,
                "created_at": now,
            }
            for source_hash, count in chars.items()
        ]
    )
    rows = await db.execute(
        asked.on_conflict_do_update(
            index_elements=["company_id", "source_hash", "language"],
            set_={"created_at": now, "chars": asked.excluded.chars},
            where=Translation.text.is_(None) & (Translation.created_at < stale_before),
        ).returning(Translation.source_hash)
    )
    return set(rows.scalars().all())


async def answer(
    db: AsyncSession, company_id: uuid.UUID, language: Language, texts: Mapping[bytes, str]
) -> None:
    if not texts:
        return
    await db.execute(
        update(Translation.__table__)  # type: ignore[arg-type]  # a Core update, one statement for all
        .where(
            Translation.company_id == company_id,
            Translation.language == language,
            Translation.source_hash == bindparam("asked_hash"),
        )
        .values(text=bindparam("answered")),
        [{"asked_hash": source_hash, "answered": text_} for source_hash, text_ in texts.items()],
    )


async def release(
    db: AsyncSession, company_id: uuid.UUID, language: Language, hashes: Sequence[bytes]
) -> None:
    """Takes back what was reserved for a call that never reached an answer: it cost nothing."""
    await db.execute(
        delete(Translation).where(
            Translation.company_id == company_id,
            Translation.language == language,
            Translation.source_hash.in_(hashes),
            Translation.text.is_(None),
        )
    )


async def chars_since(db: AsyncSession, company_id: uuid.UUID, since: datetime) -> int:
    """How much the company asked the model to translate since then, in characters."""
    total = await db.scalar(
        select(func.coalesce(func.sum(Translation.chars), 0)).where(
            Translation.company_id == company_id, Translation.created_at >= since
        )
    )
    return int(total or 0)
