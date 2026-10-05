"""The document-type registry: what kinds of file Tamga knows, each a Pydantic schema of the
fields read from it (docs/architecture/06, Documents).

The core registers the types every company has (an invoice, a bank receipt). A country pack adds
its e-documents and tax-id checks, a sector pack its own papers (a CMR); each registers from its
``register.py``, so the core never imports a pack.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class DocumentType:
    key: str
    label: str
    # "core", or the pack that registered it.
    pack: str
    # The fields an extraction reads from a file of this type.
    schema: type[BaseModel]
    # What tells a file of this type from the others, for the model that types files: one line.
    hint: str
    # A CMR or a signed delivery note: proof that a truck delivered, confirmed for one truck only.
    proof_of_delivery: bool = False


_types: dict[str, DocumentType] = {}


def register(document_type: DocumentType) -> None:
    existing = _types.get(document_type.key)
    if existing is not None and existing != document_type:
        raise ValueError(f"document type {document_type.key!r} is already registered")
    _types[document_type.key] = document_type


def get(key: str) -> DocumentType | None:
    return _types.get(key)


def all_types() -> list[DocumentType]:
    return sorted(_types.values(), key=lambda t: (t.pack != "core", t.pack, t.key))


@dataclass(frozen=True, slots=True)
class Seen:
    """What code knows of a file once it was read, for telling its type: its name, and its text
    folded as search folds it (no case, no accents, words between single spaces)."""

    filename: str
    text: str


# A file's type from what is plainly written in it, or None: rules, never a model.
type Classifier = Callable[[Seen], str | None]

_by_content: list[Classifier] = []
_by_name: list[Classifier] = []


def register_classifier(classifier: Classifier, *, by_name: bool = False) -> None:
    """A pack adds a rule that types a file by code: a CMR's printed boxes, an e-invoice's
    profile. A rule that reads only the file's name (``by_name``) is weaker: every rule on what
    the file says is asked first, whichever pack registered it."""
    (_by_name if by_name else _by_content).append(classifier)


def classify(seen: Seen) -> str | None:
    for classifier in (*_by_content, *_by_name):
        key = classifier(seen)
        if key is not None and key in _types:
            return key
    return None


class Invoice(BaseModel):
    number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    seller_name: str | None = None
    buyer_name: str | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    net_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None


class Nothing(BaseModel):
    """A file that holds nothing to read off it."""


class BankReceipt(BaseModel):
    """A payment's date and amount, as the bank printed it."""

    paid_on: date | None = None
    amount: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    payer: str | None = None
    payee: str | None = None
    reference: str | None = None


# A picture that is only part of how a mail looks. A file of this type is in no project: a mail
# that carries it brings it to none (conversations, ``carried_files``).
SIGNATURE: Final = "signature"
# A file with nothing of any job in it: a delivery failure, an app's screen, a portrait. It is in
# no project either.
NOT_A_PAPER: Final = "not_a_paper"
# The types that keep a file out of every project.
HOLDING_NOTHING: Final = frozenset({SIGNATURE, NOT_A_PAPER})
# A business card says who wrote, how to reach them and little else: a picture with an address and
# a phone in this many words or fewer is one, whatever badges sit beside it.
CARD_WORDS: Final = 60
_ADDRESS: Final = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\bwww\.[\w-]+(?:\.[\w-]+)+|https?://\S+", re.IGNORECASE
)
# Digits a phone number is written with, spaced, dashed or bracketed as people write them.
_PHONE: Final = re.compile(r"\+?\(?\d[\d\s().-]{7,}\d")
_PHONE_DIGITS: Final = 9
# A bare run of digits is as likely a tax, invoice or tracking number: it is a phone only when
# written as one (an international prefix, or digits in groups) or after a phone's label.
_GROUPED: Final = re.compile(r"\d[\s().-]+\d")
_LABELLED: Final = re.compile(
    r"\b(?:tel|telefon|phone|gsm|mobile|mob|cell|fax|t|m|p|f)\b\.?\s*:?\s*$", re.IGNORECASE
)
_DATE: Final = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")


def is_a_card(text: str) -> bool:
    """Whether a picture's text is a business card's: a mail or web address and a phone, in a
    few words."""
    if len(text.split()) > CARD_WORDS or not _ADDRESS.search(text):
        return False
    return any(_is_a_phone(text, found) for found in _PHONE.finditer(text))


def _is_a_phone(text: str, found: re.Match[str]) -> bool:
    written, before = found.group(), text[: found.start()]
    if sum(char.isdigit() for char in written) < _PHONE_DIGITS or _DATE.search(written):
        return False
    # An IBAN's digits follow its country's two capitals, in groups too.
    if before[-2:].isalpha() and before[-2:].isupper():
        return False
    return (
        written.startswith(("+", "("))
        or _GROUPED.search(written) is not None
        or _LABELLED.search(before[-16:]) is not None
    )


CORE_TYPES: Final = (
    DocumentType(
        key="invoice",
        label="Invoice",
        pack="core",
        schema=Invoice,
        hint="A bill the company sends or is sent for its own work or purchases.",
    ),
    DocumentType(
        key="bank_receipt",
        label="Bank receipt",
        pack="core",
        schema=BankReceipt,
        hint="A bank's record of one payment made or received: a transfer slip or advice.",
    ),
    DocumentType(
        key=SIGNATURE,
        label="Signature or logo",
        pack="core",
        schema=Nothing,
        hint=(
            "A picture that is only part of how a mail looks: a signature or business card (a "
            "name, a title, a phone, an address), a logo, a banner, a social icon, a "
            "certificate badge, a disclaimer, a portrait or a picture placed under a signature."
        ),
    ),
    DocumentType(
        key=NOT_A_PAPER,
        label="Nothing of a job",
        pack="core",
        schema=Nothing,
        hint=(
            "A file with nothing of any shipment, firm or payment in it: a notice that a mail "
            "could not be delivered or that someone is away, an advertisement or a greeting. "
            "A screen of an order, a list of loads or a photo of the cargo is not this."
        ),
    ),
)

for _core in CORE_TYPES:
    register(_core)
