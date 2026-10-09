"""A reference source for the core's tests, which run without any country pack (the public copy
leaves the Turkey pack out): a base in dollars, and the rates a test stores."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from app.modules.fx import repository
from app.modules.fx import service as fx
from app.modules.fx.models import FxRate
from app.platform.db import open_session

NAME = "test"


@dataclass
class Source:
    name: str = NAME
    base: str = "USD"
    kind: str = "reference"
    currencies: frozenset[str] = frozenset({"EUR", "TRY"})
    # What each day publishes; a day not listed published nothing.
    days: dict[date, list[fx.Quote]] = field(default_factory=dict)
    asked: list[date] = field(default_factory=list)

    async def read(self, on: date) -> list[fx.Quote] | None:
        self.asked.append(on)
        return self.days.get(on)


def use(monkeypatch: pytest.MonkeyPatch, country: str = "tr") -> Source:
    """Makes a test source the country's, in place of a pack's."""
    source = Source()
    monkeypatch.setitem(fx._sources, country, source)
    return source


async def store(on: date, **rates: str) -> None:
    """Stores the test source's rates for a day: ``EUR="1.16"`` is one euro in dollars."""
    async with open_session() as db:
        await repository.store(
            db,
            [
                FxRate(
                    source=NAME, base="USD", currency=code, kind="reference", on=on, rate=Decimal(v)
                )
                for code, v in rates.items()
            ],
        )
        await db.commit()
