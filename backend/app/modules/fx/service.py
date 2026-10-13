"""Official reference rates (ADR-0028): amounts across currencies, at the rate the company's country
publishes, with the source and the day named beside every converted figure.

A country pack registers the source for its zone (a central bank's daily file); a company's
`country` picks it. Rates are read once a working day by `fx.refresh` and kept in `fx_rates`. A
conversion with no rate returns nothing, and the caller says so: a figure is never guessed.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.fx import repository
from app.modules.fx.models import FxRate
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.db import open_session

logger = logging.getLogger(__name__)

_CENT: Final = Decimal("0.01")
_RATE: Final = Decimal("0.0000000001")
# The oldest rate that still stands for a day: past the longest run of holidays a central bank
# publishes nothing on. Older, the conversion says there is no rate.
MAX_AGE: Final = timedelta(days=10)
# How far back a source with nothing stored is first read.
FIRST_DAYS: Final = 14


@dataclass(frozen=True, slots=True)
class Quote:
    """One currency's rate on a day: how many units of the source's base one unit is worth."""

    currency: str
    rate: Decimal


class RateSource(Protocol):
    """A zone's official reference rates, read only (ADR-0028)."""

    name: str
    # The currency the source counts in, e.g. TRY for Turkey's central bank.
    base: str
    # Which of its rates conversions use: "buying", "selling" or "reference".
    kind: str
    # Every currency it publishes a rate for, its base aside.
    currencies: frozenset[str]

    async def read(self, on: date) -> list[Quote] | None:
        """The rates published for ``on``; None when it published none that day (a weekend, a
        holiday, or not yet)."""
        ...


_sources: dict[str, RateSource] = {}


def register(country: str, source: RateSource) -> None:
    """A country pack's reference source, used for every company in that country."""
    _sources[country] = source


def source_for(country: str) -> RateSource | None:
    return _sources.get(country)


async def reference_currencies(db: AsyncSession, actor: Actor) -> list[str]:
    """The currencies the company's reference source converts, its base first: what a policy may
    be written in. None without a source for its country."""
    country = (await companies.get_companies(db, [actor.company_id]))[actor.company_id].country
    source = _sources.get(country)
    if source is None:
        return []
    return [source.base, *sorted(set(source.currencies) - {source.base})]


def quotes(country: str, currency: str) -> bool:
    """Whether the country's source can convert this currency at all."""
    source = _sources.get(country)
    return source is not None and (currency == source.base or currency in source.currencies)


@dataclass(frozen=True, slots=True)
class Converted:
    """An amount in another currency, with how it was made. ``source`` and ``on`` are None when
    nothing was converted (the same currency)."""

    amount: Decimal
    rate: Decimal
    source: str | None
    on: date | None


async def convert(
    db: AsyncSession,
    amount: Decimal,
    currency: str | None,
    into: str,
    *,
    on: date,
    country: str,
) -> Converted | None:
    """``amount`` of ``currency`` in ``into``, at the country's reference rate published on ``on``
    or the latest day before it; a pair neither of which is the base goes through it. None when a
    rate is missing or older than ``MAX_AGE``."""
    if currency is None:
        return None
    if currency == into:
        return Converted(amount=amount, rate=Decimal(1), source=None, on=None)
    source = _sources.get(country)
    if source is None:
        return None
    # Nothing is published for a day the world has not reached: a sandbox whose clock was
    # skipped ahead reads the latest rate there is, and says which day it is from.
    on = min(on, utcnow().date())
    worth = await _in_base(db, source, currency, on)
    target = await _in_base(db, source, into, on)
    if worth is None or target is None:
        return None
    rate = worth[0] / target[0]
    days = [day for day in (worth[1], target[1]) if day is not None]
    return Converted(
        amount=(amount * rate).quantize(_CENT),
        rate=rate.quantize(_RATE),
        source=source.name,
        # The older of the two, which is the one a reader should know.
        on=min(days),
    )


async def _in_base(
    db: AsyncSession, source: RateSource, currency: str, on: date
) -> tuple[Decimal, date | None] | None:
    if currency == source.base:
        return Decimal(1), None
    row = await repository.latest(db, source.name, source.kind, currency, on, on - MAX_AGE)
    return None if row is None else (row.rate, row.on)


async def refresh(today: date) -> int:
    """Reads each source's days since its last stored one, up to ``today``, and keeps their rates.
    A day not published yet is read again on the next run. Returns how many rates were new."""
    added = 0
    for source in {source.name: source for source in _sources.values()}.values():
        async with open_session() as db:
            last = await repository.last_day(db, source.name)
        start = today - timedelta(days=FIRST_DAYS) if last is None else last + timedelta(days=1)
        day = start
        while day <= today:
            try:
                read = await source.read(day)
            except httpx.TransportError as error:  # unreachable: the next run tries again
                logger.warning("reading %s for %s failed: %s", source.name, day, error)
                break
            except Exception as error:  # a day it cannot read never stops the days after it
                logger.warning("reading %s for %s failed: %s", source.name, day, error)
                read = None
            if read:
                async with open_session() as db:
                    added += await repository.store(
                        db,
                        (
                            FxRate(
                                source=source.name,
                                base=source.base,
                                currency=quote.currency,
                                kind=source.kind,
                                on=day,
                                rate=quote.rate,
                            )
                            for quote in read
                        ),
                    )
                    await db.commit()
            day += timedelta(days=1)
    return added
