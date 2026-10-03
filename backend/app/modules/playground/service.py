"""The playground (ADR-0004, ADR-0003): what a judge's sandbox holds when it opens.

Identity creates a sandbox empty, with its guest and session, and asks for it to be seeded. The
scenarios packs register here then fill it in a job of their own, so the guest's click does not
wait on them. A scenario writes only into the sandbox it is given, through the services that own
each record, as any other source of that record would.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.platform import jobs
from app.platform.db import open_session

logger = logging.getLogger(__name__)

# A scenario: fills one sandbox, in sessions of its own, and commits what it writes.
type Seed = Callable[[uuid.UUID], Awaitable[None]]

_seeds: list[Seed] = []


def register_seed(seed: Seed) -> None:
    """Adds a scenario every new sandbox is seeded with, in the order packs register them."""
    _seeds.append(seed)


async def seed_later(db: AsyncSession, company_id: uuid.UUID) -> None:
    """Queues a new sandbox's seeding in the caller's transaction, so it runs once the sandbox
    exists and never for one whose creation rolled back."""
    from app.modules.playground import tasks

    await jobs.defer(db, tasks.seed, args={"company_id": str(company_id)})


async def seed(company_id: uuid.UUID) -> None:
    """Runs every scenario over a sandbox. Anything but a live sandbox is left alone: the job
    can outlive its sandbox's expiry, and it must never write into a pilot's records."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
    if company is None or company.kind is not CompanyKind.SANDBOX:
        logger.warning("not seeding %s: it is no sandbox", company_id)
        return
    for scenario in _seeds:
        await scenario(company_id)
