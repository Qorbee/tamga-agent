"""Facts read from text by code: what a pattern and a check digit can prove, before any model
reads anything (docs/architecture/09, ADR-0010).

The core reads what every company's text holds: IBANs, checked by their check digits, and invoice
numbers where the text names them. Packs register what they know from their ``register.py``: a
country's tax numbers and plates, a sector's consignment notes. Every extractor gets the text and
returns what it found; none reads the database, so the same text always gives the same facts.
"""

import re
from collections.abc import Callable, Iterable
from typing import Final

from app.modules.facts.service import NewFact
from app.modules.parties.identifiers import normalise
from app.modules.parties.models import IdentifierKind

type TextExtractor = Callable[[str], Iterable[NewFact]]

REGEX: Final = "regex"
INVOICE_NUMBER: Final = "invoice_number"

_extractors: dict[str, TextExtractor] = {}
# Kinds whose value names one job, or one document of one job: two items that share one belong
# together. A party's IBAN, tax number or plate is in its every mail, so none of those is a key.
_key_kinds: set[str] = {INVOICE_NUMBER}


def register(name: str, extractor: TextExtractor) -> None:
    existing = _extractors.get(name)
    if existing is not None and existing is not extractor:
        raise ValueError(f"fact extractor {name!r} is already registered")
    _extractors[name] = extractor


def register_key_kind(kind: str) -> None:
    _key_kinds.add(kind)


def key_kinds() -> frozenset[str]:
    return frozenset(_key_kinds)


def from_text(text: str) -> list[NewFact]:
    """Every fact every registered extractor finds, once per kind and value, at its best
    confidence."""
    best: dict[tuple[str, str], NewFact] = {}
    for extractor in _extractors.values():
        for fact in extractor(text):
            key = (fact.kind, fact.normalised)
            if key not in best or fact.confidence > best[key].confidence:
                best[key] = fact
    return list(best.values())


def reference(value: str) -> str:
    """The form a number is compared in: upper case, letters and digits only."""
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def after_label(label: str, value: str = r"[A-Z0-9][A-Z0-9/.\-]{2,30}") -> re.Pattern[str]:
    """A value written after its label ("Invoice No: 2026/118"), on the same line."""
    return re.compile(
        rf"(?:{label})\b\s*(?:no\b|nr\b|number\b|#)?\.?\s*[:#.\-]?\s*({value})", re.IGNORECASE
    )


def is_reference(value: str) -> bool:
    """A number worth comparing: it has a digit, and is not a date written where one could be."""
    return any(char.isdigit() for char in value) and not _DATE.fullmatch(value)


def is_quantity(text: str, end: int, units: Iterable[str] = ()) -> bool:
    """Whether a unit follows the number ending at ``end``: "2400 kg" is a weight, not a reference.
    A pack passes the units its language writes."""
    words = "|".join(re.escape(unit) for unit in (*_UNITS, *units))
    return re.match(rf"\s*(?:{words})(?![^\W\d_])", text[end:], re.IGNORECASE) is not None


# Groups of four, as IBANs are printed, or run together; letters in the account part for the
# countries that use them. Checked by mod 97 below, so a pattern this loose finds no false one.
_IBAN: Final = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_INVOICE: Final = after_label(r"\binvoice")
_DATE: Final = re.compile(r"\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}")
_UNITS: Final = (
    "kg", "kgs", "t", "ton", "tons", "tonnes", "lb", "lbs", "m3", "cbm", "ldm", "km", "pcs",
    "pallet", "pallets", "plt", "cartons", "boxes",
)  # fmt: skip


def ibans(text: str) -> Iterable[NewFact]:
    for match in _IBAN.finditer(text):
        written = match.group(0)
        compact = written.replace(" ", "")
        # The pattern can take a word after the IBAN ("… 01 TL"): the longest prefix whose check
        # digits hold is the IBAN.
        for end in range(len(compact), 14, -1):
            iban = normalise(IdentifierKind.IBAN, compact[:end], country="")
            if iban is not None:
                yield NewFact("iban", _written_prefix(written, end), iban, 0.99, REGEX)
                break


def _written_prefix(written: str, characters: int) -> str:
    """The IBAN as it was written, spaces kept, up to its last character."""
    seen = 0
    for index, char in enumerate(written):
        seen += char != " "
        if seen == characters:
            return written[: index + 1]
    return written


def invoice_numbers(text: str) -> Iterable[NewFact]:
    for match in _INVOICE.finditer(text):
        number = match.group(1).rstrip(".-/")
        if is_reference(number):
            yield NewFact(INVOICE_NUMBER, number, reference(number), 0.85, REGEX)


register("iban", ibans)
register("invoice_number", invoice_numbers)
