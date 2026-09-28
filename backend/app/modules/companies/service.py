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
