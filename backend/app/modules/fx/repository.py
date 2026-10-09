from collections.abc import Iterable
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.fx.models import FxRate


async def store(db: AsyncSession, rates: Iterable[FxRate]) -> int:
    """Adds the rates not stored yet, so a day read again changes nothing; returns how many were
    new."""
    rows = [
        {
            "source": rate.source,
            "base": rate.base,
            "currency": rate.currency,
            "kind": rate.kind,
            "on": rate.on,
            "rate": rate.rate,
        }
        for rate in rates
    ]
    if not rows:
        return 0
    added = await db.scalars(
        insert(FxRate)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["source", "currency", "kind", "on"])
        .returning(FxRate.id)
    )
    return len(added.all())


async def last_day(db: AsyncSession, source: str) -> date | None:
    """The latest day a source's rates are stored for."""
    day: date | None = await db.scalar(select(func.max(FxRate.on)).where(FxRate.source == source))
    return day


async def latest(
    db: AsyncSession, source: str, kind: str, currency: str, on: date, since: date
) -> FxRate | None:
    """The currency's rate published on ``on`` or the latest day before it, back to ``since``."""
    rate: FxRate | None = await db.scalar(
        select(FxRate)
        .where(
            FxRate.source == source,
            FxRate.kind == kind,
            FxRate.currency == currency,
            FxRate.on <= on,
            FxRate.on >= since,
        )
        .order_by(FxRate.on.desc())
        .limit(1)
    )
    return rate
