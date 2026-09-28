"""Companies: the tenants every business row belongs to (ADR-0003).

These operations run inside another module's transaction (sign-in, guest sessions), so they flush
and never commit; the operation that called them commits once.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import repository
from app.modules.companies.models import Company, CompanyKind, CompanyMode

# A pilot starts in shadow: beside real work, sending nothing (ADR-0009). Demo companies and
# sandboxes are live only inside simulated channels that reach no real person.
_INITIAL_MODE: Final[dict[CompanyKind, CompanyMode]] = {
    CompanyKind.PILOT: CompanyMode.SHADOW,
    CompanyKind.DEMO: CompanyMode.LIVE,
    CompanyKind.SANDBOX: CompanyMode.LIVE,
}


async def create_company(
    db: AsyncSession, *, name: str, kind: CompanyKind, expires_at: datetime | None = None
) -> Company:
    company = Company(name=name, kind=kind, mode=_INITIAL_MODE[kind], expires_at=expires_at)
    return await repository.add(db, company)


async def get_companies(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Company]:
    return {company.id: company for company in await repository.get_many(db, ids)}


async def count_live_sandboxes(db: AsyncSession, now: datetime) -> int:
    """Also serialises sandbox creation until the caller commits: call it right before creating."""
    return await repository.count_live_sandboxes(db, now)


async def expired_sandboxes(db: AsyncSession, now: datetime, *, limit: int) -> list[uuid.UUID]:
    return await repository.expired_sandboxes(db, now, limit)


async def delete_sandbox(db: AsyncSession, company_id: uuid.UUID) -> None:
    """Deletes a sandbox and, by cascade, every row it owns. Only a sandbox: a pilot's data is
    never deleted by this path."""
    company = (await get_companies(db, [company_id])).get(company_id)
    if company is None:
        return
    if company.kind is not CompanyKind.SANDBOX:
        raise ValueError("only a sandbox is deleted on expiry")
    await repository.delete_company(db, company_id)
