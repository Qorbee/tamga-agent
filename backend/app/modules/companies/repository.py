import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies.models import Company, CompanyKind
from app.platform.language import Language


async def add(db: AsyncSession, company: Company) -> Company:
    db.add(company)
    await db.flush()
    return company


async def get_many(db: AsyncSession, ids: Sequence[uuid.UUID]) -> list[Company]:
    rows = await db.scalars(select(Company).where(Company.id.in_(ids)))
    return list(rows)


async def set_language(db: AsyncSession, company_id: uuid.UUID, language: Language) -> None:
    await db.execute(update(Company).where(Company.id == company_id).values(language=language))


async def count_live_sandboxes(db: AsyncSession, now: datetime) -> int:
    # Held until the caller's transaction ends, so a check-then-create cannot interleave with
    # another: the cap holds under a burst.
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext("tamga.sandbox_cap"))))
    count = await db.scalar(
        select(func.count())
        .select_from(Company)
        .where(Company.kind == CompanyKind.SANDBOX, Company.expires_at > now)
    )
    return count or 0


async def expired_sandboxes(db: AsyncSession, now: datetime, limit: int) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Company.id)
        .where(Company.kind == CompanyKind.SANDBOX, Company.expires_at <= now)
        .order_by(Company.expires_at)
        .limit(limit)
    )
    return list(rows)


async def delete_company(db: AsyncSession, company_id: uuid.UUID) -> None:
    await db.execute(delete(Company).where(Company.id == company_id))
