import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.playground.models import World


async def add(db: AsyncSession, world: World) -> None:
    db.add(world)
    await db.flush()


async def get(db: AsyncSession, company_id: uuid.UUID, *, lock: bool = False) -> World | None:
    query = select(World).where(World.company_id == company_id)
    if lock:
        query = query.with_for_update()
    world: World | None = await db.scalar(query)
    return world


async def set_played(db: AsyncSession, company_id: uuid.UUID, played: int) -> None:
    await db.execute(update(World).where(World.company_id == company_id).values(played=played))


async def unfinished(db: AsyncSession, steps: dict[str, int]) -> list[World]:
    """The worlds of these scenarios with steps left to play."""
    if not steps:
        return []
    rows = await db.scalars(
        select(World).where(
            World.opened_on.is_not(None),
            or_(*(and_(World.scenario == key, World.played < n) for key, n in steps.items())),
        )
    )
    return list(rows)
