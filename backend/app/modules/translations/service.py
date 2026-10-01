"""Translations: what Tamga wrote, read in another language, with the original left as it is.

- A translation is asked for by the text itself and kept under its hash, per company: the same
  text is translated once, and a text written again is another text.
- A model writes it, within the company's daily budget. What is sent is reserved first, one
  request at a time per company, so two requests cannot both spend the same allowance, and a text
  whose answer could not be used still counts and is not sent again that day.
- Where there is no translation to give, the answer holds null and the reader shows the text as
  written.
"""

import hashlib
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from pydantic_ai import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.companies import service as companies
from app.modules.translations import agents, repository
from app.modules.translations.schemas import TranslationOut
from app.platform import llm
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.language import Language

# Texts a call: a model asked for too many at once drops one, and the whole answer is lost.
_BATCH: Final = 40
_LIMITS: Final = UsageLimits(request_limit=3)
_DAY: Final = timedelta(days=1)


def _hash(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


async def translate(
    db: AsyncSession, actor: Actor, *, language: Language, texts: Sequence[str]
) -> TranslationOut:
    """Each text in ``language``, from what is kept or from the model; null where neither has
    it. Commits what it reserves and keeps."""
    asked = [text.strip() for text in texts]
    hashes = {text: _hash(text) for text in asked if text}
    known = await repository.find(db, actor.company_id, language, list(hashes.values()))
    missing = [text for text in hashes if hashes[text] not in known]
    if missing:
        known |= await _write(db, actor.company_id, language, missing)
    return TranslationOut(texts=[known.get(hashes[text]) if text else text for text in asked])


async def _write(
    db: AsyncSession, company_id: uuid.UUID, language: Language, texts: Sequence[str]
) -> dict[bytes, str]:
    """Has the model translate ``texts``, as many as the day's budget still holds, and keeps
    them. No transaction is open while the model writes."""
    now = utcnow()
    await repository.lock(db, company_id)
    left = get_settings().translated_chars_per_day - await repository.chars_since(
        db, company_id, now - _DAY
    )
    fits = {}
    for text in texts:
        if len(text) <= left:
            fits[_hash(text)] = text
            left -= len(text)
    reserved = await repository.reserve(
        db,
        company_id,
        language,
        {source_hash: len(text) for source_hash, text in fits.items()},
        now=now,
        stale_before=now - _DAY,
    )
    company = (await companies.get_companies(db, [company_id])).get(company_id)
    trace_content = company is not None and companies.traces_content(company.kind)
    # Ends the transaction, and with it the company's lock: the reservation is what holds now.
    await db.commit()
    taken = [text for source_hash, text in fits.items() if source_hash in reserved]
    written: dict[bytes, str] = {}
    try:
        for start in range(0, len(taken), _BATCH):
            batch = taken[start : start + _BATCH]
            try:
                run = await llm.run(
                    agents.translator,
                    agents.case(language, batch),
                    deps=batch,
                    limits=_LIMITS,
                    language=None,
                    trace_content=trace_content,
                )
            except llm.NoAnswer:
                # Paid for and unusable: its texts stay reserved, so they are not sent again today.
                continue
            for text, translated in zip(batch, run.output.texts, strict=True):
                cleaned = " ".join(translated.replace("\x00", "").split())
                if cleaned:
                    written[_hash(text)] = cleaned
    except Exception:
        # The provider was not reached, or failed: what got no answer was not paid for.
        unanswered = [source_hash for source_hash in reserved if source_hash not in written]
        await repository.answer(db, company_id, language, written)
        await repository.release(db, company_id, language, unanswered)
        await db.commit()
        raise
    await repository.answer(db, company_id, language, written)
    await db.commit()
    return written
