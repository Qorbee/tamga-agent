"""The document-type registry: what kinds of file Tamga knows, each a Pydantic schema of the
fields read from it (docs/architecture/06, Documents).

The core registers the types every company has (an invoice, a bank receipt). A country pack adds
its e-documents and tax-id checks, a sector pack its own papers (a CMR); each registers from its
``register.py``, so the core never imports a pack.
"""

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


class BankReceipt(BaseModel):
    """A payment's date and amount, as the bank printed it."""

    paid_on: date | None = None
    amount: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    payer: str | None = None
    payee: str | None = None
    reference: str | None = None


CORE_TYPES: Final = (
    DocumentType(key="invoice", label="Invoice", pack="core", schema=Invoice),
    DocumentType(key="bank_receipt", label="Bank receipt", pack="core", schema=BankReceipt),
)

for _core in CORE_TYPES:
    register(_core)
