"""Amounts across currencies at the official reference rate of the company's country (ADR-0028):
through the source's base, at the latest rate published on or before the day, never guessed."""

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

from app.modules.fx import service as fx
from app.platform.db import open_session
from app.tests.fx import rates

pytestmark = pytest.mark.integration

FRIDAY = date(2025, 10, 10)


@pytest.fixture(autouse=True)
async def no_rates(database: None) -> None:
    """None of the rates every database test starts with: these tests store their own."""
    async with open_session() as db:
        await db.execute(text("DELETE FROM fx_rates"))
        await db.commit()


async def test_an_amount_goes_through_the_base_with_its_source_and_day(
    database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    rates.use(monkeypatch)
    await rates.store(FRIDAY, EUR="1.16", TRY="0.02")
    async with open_session() as db:
        in_dollars = await fx.convert(db, Decimal("1900.00"), "EUR", "USD", on=FRIDAY, country="tr")
        # Neither side is the base: euros to lira through the dollar.
        in_lira = await fx.convert(db, Decimal("100.00"), "EUR", "TRY", on=FRIDAY, country="tr")
        same = await fx.convert(db, Decimal("5.00"), "TRY", "TRY", on=FRIDAY, country="tr")
    assert in_dollars == fx.Converted(Decimal("2204.00"), Decimal("1.16"), "test", FRIDAY)
    assert in_lira is not None and in_lira.amount == Decimal("5800.00")
    assert in_lira.rate == Decimal("58.0000000000")
    assert same == fx.Converted(Decimal("5.00"), Decimal(1), None, None)


async def test_a_weekend_uses_the_last_published_day_and_says_which(
    database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    rates.use(monkeypatch)
    await rates.store(FRIDAY, EUR="1.16")
    await rates.store(FRIDAY - timedelta(days=1), EUR="1.10")
    async with open_session() as db:
        sunday = await fx.convert(
            db, Decimal("10.00"), "EUR", "USD", on=FRIDAY + timedelta(days=2), country="tr"
        )
    assert sunday is not None and sunday.on == FRIDAY and sunday.amount == Decimal("11.60")


async def test_a_rate_is_never_guessed(database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    rates.use(monkeypatch)
    await rates.store(FRIDAY, EUR="1.16")
    async with open_session() as db:
        # Nothing for this currency, a rate too old to stand, a country with no source.
        assert await fx.convert(db, Decimal(1), "TRY", "USD", on=FRIDAY, country="tr") is None
        assert (
            await fx.convert(
                db, Decimal(1), "EUR", "USD", on=FRIDAY + fx.MAX_AGE + timedelta(1), country="tr"
            )
            is None
        )
        assert await fx.convert(db, Decimal(1), "EUR", "USD", on=FRIDAY, country="xx") is None
        assert await fx.convert(db, Decimal(1), None, "USD", on=FRIDAY, country="tr") is None
    assert fx.quotes("tr", "EUR") and fx.quotes("tr", "USD")
    assert not fx.quotes("tr", "XYZ") and not fx.quotes("xx", "USD")


async def test_refresh_reads_each_day_once_and_a_day_not_published_again(
    database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = rates.use(monkeypatch)
    source.days[FRIDAY] = [fx.Quote("EUR", Decimal("1.16")), fx.Quote("TRY", Decimal("0.02"))]

    # Nothing stored yet: the first run reads back FIRST_DAYS.
    assert await fx.refresh(FRIDAY) == 2
    assert source.asked[0] == FRIDAY - timedelta(days=fx.FIRST_DAYS)
    assert source.asked[-1] == FRIDAY

    # The next runs start after the last day stored; the weekend, unpublished, is asked again.
    source.asked.clear()
    assert await fx.refresh(FRIDAY + timedelta(days=2)) == 0
    assert await fx.refresh(FRIDAY + timedelta(days=2)) == 0
    saturday, sunday = FRIDAY + timedelta(days=1), FRIDAY + timedelta(days=2)
    assert source.asked == [saturday, sunday, saturday, sunday]


async def test_a_day_it_cannot_read_never_stops_the_days_after_it(
    database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = rates.use(monkeypatch)
    thursday = FRIDAY - timedelta(days=1)
    source.days[FRIDAY] = [fx.Quote("EUR", Decimal("1.16"))]
    read = source.read

    async def garbled(on: date) -> list[fx.Quote] | None:
        if on == thursday:
            raise ValueError("not a bulletin")
        return await read(on)

    monkeypatch.setattr(source, "read", garbled)
    assert await fx.refresh(FRIDAY) == 1


async def test_an_unreachable_source_waits_for_the_next_run(
    database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = rates.use(monkeypatch)
    source.days[FRIDAY] = [fx.Quote("EUR", Decimal("1.16"))]

    async def unreachable(on: date) -> list[fx.Quote] | None:
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(source, "read", unreachable)
    assert await fx.refresh(FRIDAY) == 0
