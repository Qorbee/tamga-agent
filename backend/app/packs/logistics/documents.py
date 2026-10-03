"""The logistics pack's document types: what a truck's paperwork says."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, Field

from app.modules.documents.registry import DocumentType, Seen
from app.platform.search import fold


class Cmr(BaseModel):
    """The international consignment note; signed at delivery, it is the truck's proof."""

    cmr_number: str | None = None
    sender: str | None = None
    consignee: str | None = None
    carrier: str | None = None
    place_of_loading: str | None = None
    place_of_delivery: str | None = None
    truck_plate: str | None = None
    trailer_plate: str | None = None
    loading_date: date | None = None
    delivery_date: date | None = None
    signed_on_delivery: bool | None = None


class ProofOfDeliveryPhoto(BaseModel):
    truck_plate: str | None = None
    delivered_at: datetime | None = None
    signed: bool | None = None


class LoadingPhoto(BaseModel):
    """A truck photographed as it was loaded: which truck, and when."""

    truck_plate: str | None = None
    trailer_plate: str | None = None
    taken_at: datetime | None = None


class TransportOrder(BaseModel):
    """The client's order for a job: what to carry, from where to where, when, for how much."""

    order_number: str | None = None
    client: str | None = None
    place_of_loading: str | None = None
    place_of_delivery: str | None = None
    loading_date: date | None = None
    delivery_date: date | None = None
    cargo: str | None = None
    trucks: int | None = None
    price: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class PositionRow(BaseModel):
    plate: str
    place: str | None = None
    status: str | None = None
    reported_at: datetime | None = None


class PositionSheet(BaseModel):
    """A dispatcher's spreadsheet, or its photo: one row per truck."""

    rows: list[PositionRow] = []


TYPES: Final = (
    DocumentType(
        key="cmr",
        label="CMR consignment note",
        pack="logistics",
        schema=Cmr,
        proof_of_delivery=True,
    ),
    DocumentType(
        key="pod_photo",
        label="Proof-of-delivery photo",
        pack="logistics",
        schema=ProofOfDeliveryPhoto,
        proof_of_delivery=True,
    ),
    DocumentType(
        key="position_sheet",
        label="Dispatcher's position sheet",
        pack="logistics",
        schema=PositionSheet,
    ),
    DocumentType(key="loading_photo", label="Loading photo", pack="logistics", schema=LoadingPhoto),
    DocumentType(
        key="transport_order", label="Transport order", pack="logistics", schema=TransportOrder
    ),
)

# The boxes the CMR form prints, in the convention's languages: a form prints them in two or
# three at once. A file with three of them is a CMR; a mail or an order that names a sender, a
# consignee and a carrier is not, so those plain words are not among them.
CMR_LABELS: Final = tuple(
    fold(label)
    for label in (
        "cmr",
        "consignment note",
        "lettre de voiture",
        "Frachtbrief",
        "place of delivery of the goods",
        "lieu prévu pour la livraison",
        "Auslieferungsort des Gutes",
        "place and date of taking over the goods",
        "prise en charge de la marchandise",
        "Übernahme des Gutes",
        "successive carriers",
        "transporteurs successifs",
        "nachfolgende Frachtführer",
        "reservations and observations",
        "réserves et observations",
        "Vorbehalte und Bemerkungen",
        "documents attached",
        "documents annexés",
        "beigefügte Dokumente",
        "sender's instructions",
        "instructions de l'expéditeur",
        "Anweisungen des Absenders",
        "special agreements",
        "conventions particulières",
        "besondere Vereinbarungen",
        "goods received",
        "marchandises reçues",
        "Gut empfangen",
        "marks and nos",
        "marques et numéros",
        "Kennzeichen und Nummern",
        "method of packing",
        "mode d'emballage",
        "Art der Verpackung",
        "nature of the goods",
        "nature de la marchandise",
        "Bezeichnung des Gutes",
    )
)
_CMR_NAME: Final = re.compile(r"(?<![a-z])cmr(?![a-z])", re.IGNORECASE)


def classify(seen: Seen) -> str | None:
    """A CMR by its printed boxes, each a whole phrase of its text."""
    padded = f" {seen.text} "
    return "cmr" if sum(f" {label} " in padded for label in CMR_LABELS) >= 3 else None


def classify_by_name(seen: Seen) -> str | None:
    """A CMR by its name, when what it says named nothing surer."""
    return "cmr" if _CMR_NAME.search(seen.filename) else None
