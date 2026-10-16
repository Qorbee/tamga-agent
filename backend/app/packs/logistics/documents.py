"""The logistics pack's document types: what a truck's paperwork says."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, Field

from app.modules.books.charges import Charge
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


class WeighbridgeTicket(BaseModel):
    """A weighbridge's slip: a truck weighed at a border, a port or a site."""

    truck_plate: str | None = None
    trailer_plate: str | None = None
    weight_kg: Decimal | None = None
    weighed_at: datetime | None = None
    place: str | None = None
    ticket_number: str | None = None


class CustomsDeclaration(BaseModel):
    """An export, import or transit declaration: its number (an MRN), office and state."""

    declaration_number: str | None = None
    customs_office: str | None = None
    registered_on: date | None = None
    status: str | None = None
    sender: str | None = None
    consignee: str | None = None
    truck_plate: str | None = None


class CommercialInvoice(BaseModel):
    """The shipper's invoice for the goods a truck carries, made for customs."""

    number: str | None = None
    issue_date: date | None = None
    seller: str | None = None
    buyer: str | None = None
    total_amount: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class PackingList(BaseModel):
    """What a load holds: packages, pallets, weight and volume."""

    packages: int | None = None
    gross_weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None


class DriverPapers(BaseModel):
    """A driver's identity, licence, visa or permit, sent for a border crossing."""

    driver_name: str | None = None
    document_number: str | None = None
    valid_until: date | None = None


TYPES: Final = (
    DocumentType(
        key="cmr",
        label="CMR consignment note",
        pack="logistics",
        schema=Cmr,
        proof_of_delivery=True,
        hint="The international consignment note with its numbered boxes, often stamped.",
    ),
    DocumentType(
        key="pod_photo",
        label="Proof-of-delivery photo",
        pack="logistics",
        schema=ProofOfDeliveryPhoto,
        proof_of_delivery=True,
        hint="A photo of goods unloaded or a delivery note signed at the destination.",
    ),
    DocumentType(
        key="position_sheet",
        label="Dispatcher's position sheet",
        pack="logistics",
        schema=PositionSheet,
        hint="A list of trucks with where each is now, or a screenshot of one.",
    ),
    DocumentType(
        key="loading_photo",
        label="Loading photo",
        pack="logistics",
        schema=LoadingPhoto,
        hint="A photo of a truck, its trailer or its load being loaded.",
    ),
    DocumentType(
        key="transport_order",
        label="Transport order",
        pack="logistics",
        schema=TransportOrder,
        hint="A client's order or loading instructions for a job: what, from where, to where.",
    ),
    DocumentType(
        key="weighbridge_ticket",
        label="Weighbridge slip",
        pack="logistics",
        schema=WeighbridgeTicket,
        hint="A printed slip of a truck's weight at a weighbridge, with its plate.",
    ),
    DocumentType(
        key="customs_declaration",
        label="Customs declaration",
        pack="logistics",
        schema=CustomsDeclaration,
        hint=(
            "An export, import or transit declaration, an MRN, a T1, a TIR carnet, or a "
            "screenshot of a customs system showing one."
        ),
    ),
    DocumentType(
        key="commercial_invoice",
        label="Commercial invoice",
        pack="logistics",
        schema=CommercialInvoice,
        hint="The shipper's invoice for the goods carried, made for customs: not a freight bill.",
    ),
    DocumentType(
        key="packing_list",
        label="Packing list",
        pack="logistics",
        schema=PackingList,
        hint="A list of the packages, pallets, weights and volumes of a load.",
    ),
    DocumentType(
        key="driver_papers",
        label="Driver's papers",
        pack="logistics",
        schema=DriverPapers,
        hint="A driver's passport, identity card, licence, visa or permit.",
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


# What a freight bill charges for, as the bill reader tells the lines apart.
CHARGES: Final = (
    Charge("freight", "carrying a load from one place to another (navlun)"),
    Charge("waiting", "a truck kept waiting at a border, a site or a customs office (bekleme)"),
    Charge("late_delivery", "a penalty or deduction for delivering late"),
    Charge("return_trip", "a truck's empty or loaded trip back"),
    Charge("customs", "customs clearance, declarations, duties, a broker's fee"),
    Charge("handling", "loading, unloading, cranes, warehouse handling"),
    Charge("insurance", "cargo or transport insurance"),
)
