"""What code keeps of the bill reader's answer (ADR-0023): a line only when its words are in the
bill's file; of each line, an amount, days, a place or a plate only when the row of the file its
words stand on holds it; and whether the lines add up to the bill's figures. A model's reading,
checked: it proposes, never decides."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from app.modules.books.agents import BillRead, ReadLine
from app.modules.facts.extractors import reference

# How far the lines' sum may be from the bill's figure and still add up: rounding on each line,
# or a cent of tax split badly. The larger of this and the share below.
_LEEWAY: Final = Decimal("1.00")
_LEEWAY_SHARE: Final = Decimal("0.005")
# A figure as a row writes it: "1.000,00", "1,000.00", "1 000", "300".
_FIGURE: Final = re.compile(r"\d(?:[\d.,' ]*\d)?")


@dataclass(frozen=True, slots=True)
class Checked:
    about: str
    kind: str
    lines: list[ReadLine]
    # Whether the lines' amounts add up to the bill's net or total; None when they give none to
    # add, or another currency than the bill's.
    balance: bool | None
    # Lines left out because their words are not in the file.
    dropped: int


def _rows(text: str) -> str:
    """The text with each row's spaces made one and its case folded, rows kept apart: one
    character for each row's end, so a place in it is the same place in the text squashed."""
    squashed = (" ".join(row.split()) for row in text.splitlines())
    return "\n".join(row for row in squashed if row).casefold()


def _row_of(rows: str, quote: str) -> str | None:
    """The rows of the file the quote stands on, whole; None when the file does not hold it."""
    at = rows.replace("\n", " ").find(quote)
    if at < 0:
        return None
    start = rows.rfind("\n", 0, at) + 1
    end = rows.find("\n", at + len(quote))
    return rows[start : end if end >= 0 else len(rows)]


def _figures(row: str) -> set[Decimal]:
    """Every value a figure in the row can be, read with either mark as the decimal one: a space
    may part a figure's thousands ("1 000,00") or two figures ("1234 1,000.00"), so each is
    read both ways."""
    values: set[Decimal] = set()
    for written in _FIGURE.findall(row):
        for bare in {written.replace(" ", ""), *written.split()}:
            bare = bare.replace("'", "")
            for decimal, thousands in ((".", ","), (",", ".")):
                try:
                    values.add(Decimal(bare.replace(thousands, "").replace(decimal, ".")))
                except InvalidOperation:
                    continue
    return values


def _holds_plate(row: str, plate: str) -> bool:
    """Whether the row writes the plate whole, its groups apart or not: never as a part of a
    longer one."""
    spaced = r"[\s.-]?".join(re.escape(char) for char in plate.casefold())
    return re.search(rf"(?<![0-9a-z]){spaced}(?![0-9a-z])", row) is not None


def checked(
    read: BillRead,
    text: str,
    *,
    currency: str | None,
    net: Decimal | None,
    total: Decimal | None,
) -> Checked:
    """The reader's answer as code keeps it, against the file's ``text`` and the bill's figures
    in the books."""
    rows = _rows(text)
    kept: list[ReadLine] = []
    for line in read.lines:
        quote = " ".join(line.quote.split()).casefold()
        row = _row_of(rows, quote) if quote else None
        if row is None:
            continue
        plates = list(
            dict.fromkeys(
                plate
                for plate in (reference(written) for written in line.plates)
                if 4 <= len(plate) <= 10 and any(char.isalpha() for char in plate)
                if _holds_plate(row, plate)
            )
        )
        kept.append(
            line.model_copy(
                update={
                    "quote": " ".join(line.quote.split()),
                    "plates": plates,
                    "amount": line.amount if line.amount in _figures(row) else None,
                    "days": line.days if line.days in _figures(row) else None,
                    "origin": _named(line.origin, row),
                    "destination": _named(line.destination, row),
                }
            )
        )
    return Checked(
        about=" ".join(read.about.split())[:300],
        kind=read.kind,
        lines=kept,
        balance=_balance(kept, currency=currency, net=net, total=total),
        dropped=len(read.lines) - len(kept),
    )


def _named(place: str | None, row: str) -> str | None:
    """A place the row writes, as the reader gave it; None when the row does not."""
    if not place or " ".join(place.split()).casefold() not in row:
        return None
    return " ".join(place.split())


def _balance(
    lines: list[ReadLine], *, currency: str | None, net: Decimal | None, total: Decimal | None
) -> bool | None:
    amounts = [line.amount for line in lines if line.amount is not None]
    if not amounts or not lines:
        return None
    if currency and any(
        line.currency and line.currency.upper() != currency.upper() for line in lines
    ):
        return None
    summed = sum(amounts, Decimal(0))
    figures = [figure for figure in (net, total) if figure is not None]
    if not figures:
        return None
    return any(
        abs(summed - figure) <= max(_LEEWAY, abs(figure) * _LEEWAY_SHARE) for figure in figures
    )
