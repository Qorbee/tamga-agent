"""The logistics pack's document types: what a truck's paperwork says."""

from datetime import date, datetime
from typing import Final

from pydantic import BaseModel

from app.modules.documents.registry import DocumentType


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
)
