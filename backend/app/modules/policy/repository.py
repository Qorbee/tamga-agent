import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.policy.models import Policy, PolicyDraft


async def current(db: AsyncSession, company_id: uuid.UUID) -> Policy | None:
    policy: Policy | None = await db.scalar(
        select(Policy)
        .where(Policy.company_id == company_id)
        .order_by(Policy.version.desc())
        .limit(1)
    )
    return policy


async def lock_versions(db: AsyncSession, company_id: uuid.UUID) -> None:
    """Two owners saving at once draw versions one after the other, until the transaction ends."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"policies:{company_id}"},
    )


async def add(db: AsyncSession, policy: Policy) -> None:
    db.add(policy)
    await db.flush()


async def get_draft(
    db: AsyncSession, company_id: uuid.UUID, draft_id: uuid.UUID, *, for_update: bool = False
) -> PolicyDraft | None:
    query = select(PolicyDraft).where(
        PolicyDraft.company_id == company_id, PolicyDraft.id == draft_id
    )
    draft: PolicyDraft | None = await db.scalar(query.with_for_update() if for_update else query)
    return draft


async def add_draft(db: AsyncSession, draft: PolicyDraft) -> None:
    db.add(draft)
    await db.flush()
