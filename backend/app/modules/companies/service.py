"""Companies: the tenants every business row belongs to (ADR-0003).

These operations run inside another module's transaction (sign-in, guest sessions), so they flush
and never commit; the operation that called them commits once.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import repository
from app.modules.companies.models import Company, CompanyDomain, CompanyKind, CompanyMode
from app.platform.language import Language

# A pilot starts in shadow: beside real work, sending nothing (ADR-0009). Demo companies and
# sandboxes are live only inside simulated channels that reach no real person.
_INITIAL_MODE: Final[dict[CompanyKind, CompanyMode]] = {
    CompanyKind.PILOT: CompanyMode.SHADOW,
    CompanyKind.DEMO: CompanyMode.LIVE,
    CompanyKind.SANDBOX: CompanyMode.LIVE,
}


def traces_content(kind: CompanyKind) -> bool:
    """Whether an agent run's prompts and answers may go to tracing (ADR-0007): only for a company
    whose data is synthetic. A pilot's, its replay's included, never leave the server."""
    return kind in (CompanyKind.DEMO, CompanyKind.SANDBOX)


async def create_company(
    db: AsyncSession, *, name: str, kind: CompanyKind, expires_at: datetime | None = None
) -> Company:
    company = Company(name=name, kind=kind, mode=_INITIAL_MODE[kind], expires_at=expires_at)
    return await repository.add(db, company)


async def get_companies(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Company]:
    return {company.id: company for company in await repository.get_many(db, ids)}


async def set_language(db: AsyncSession, company_id: uuid.UUID, language: Language) -> None:
    """What Tamga writes from now on is in ``language``; what it wrote before stays as it is."""
    await repository.set_language(db, company_id, language)


async def set_tax_id(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    tax_id: str,
    legal_name: str | None,
    registry_title: str | None,
    registry_domain: str | None,
    checked_at: datetime | None,
) -> None:
    """Who the company is to the tax office. Only modules/profile calls it, after its checks."""
    await repository.set_tax_id(
        db,
        company_id,
        tax_id=tax_id,
        legal_name=legal_name,
        registry_title=registry_title,
        registry_domain=registry_domain,
        checked_at=checked_at,
    )


async def domain_words(db: AsyncSession, company_id: uuid.UUID) -> list[CompanyDomain]:
    """The owner's word on each mail domain they named: the company's or not."""
    return await repository.domain_words(db, company_id)


async def set_domain_word(
    db: AsyncSession, company_id: uuid.UUID, domain: str, *, ours: bool
) -> None:
    await repository.set_domain_word(db, company_id, domain, ours=ours)


async def forget_domain_word(db: AsyncSession, company_id: uuid.UUID, domain: str) -> None:
    await repository.forget_domain_word(db, company_id, domain)


async def advance_clock(db: AsyncSession, company_id: uuid.UUID, by: timedelta) -> None:
    """Moves a sandbox's business time forward ("skip 24 hours"); any other company's clock is
    the wall clock, and is left as it is."""
    await repository.advance_clock(db, company_id, by)


async def count_live_sandboxes(db: AsyncSession, now: datetime) -> int:
    """Also serialises sandbox creation until the caller commits: call it right before creating."""
    return await repository.count_live_sandboxes(db, now)


async def awake_companies(db: AsyncSession, now: datetime) -> list[uuid.UUID]:
    """The one read across companies of those someone may open today, of their ids alone."""
    return await repository.awake(db, now)


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
