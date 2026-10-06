import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.projects.models import FieldSource
from app.packs.logistics.models import (
    LocationContact,
    LocationSource,
    ShipmentState,
    ShipmentStatus,
)
from app.platform.items import ItemKind


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    reported_at: datetime
    source: LocationSource
    raw_text: str
    place: str | None
    latitude: float | None
    longitude: float | None
    status: ShipmentStatus | None
    created_at: datetime


class LocationPage(BaseModel):
    items: list[LocationOut]
    total: int
    limit: int
    offset: int


class LocationCreate(BaseModel):
    # Left out: now, in the company's business time.
    reported_at: datetime | None = None
    place: str | None = Field(default=None, max_length=300)
    raw_text: str = Field(default="", max_length=2000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    status: ShipmentStatus | None = None


class ShipmentFields(BaseModel):
    """The editable fields of a truck. Each one sent is the owner's from then on."""

    truck_plate: str | None = Field(default=None, max_length=20)
    trailer_plate: str | None = Field(default=None, max_length=20)
    driver_name: str | None = Field(default=None, max_length=200)
    driver_phone: str | None = Field(default=None, max_length=40)
    subcontractor_id: uuid.UUID | None = None
    dispatcher_id: uuid.UUID | None = None
    location_contact: LocationContact | None = None
    loading_date: date | None = None
    delivery_date: date | None = None
    status: ShipmentStatus | None = None


class ShipmentCreate(ShipmentFields):
    project_id: uuid.UUID


class ShipmentUpdate(ShipmentFields):
    # The owner's answer to a truck code proposed. Rejecting one takes code's papers off it.
    state: Literal["confirmed", "rejected"] | None = None


class ShipmentHitch(BaseModel):
    """The trailer a truck that has none takes: a plate as the board showed it, with the truck row
    it stands on when it is another truck's lone plate (that row is then no truck of the job, and
    one whose plate changed since is refused), or none for a plate seen on the job's papers or
    mail."""

    plate: str = Field(min_length=1, max_length=20)
    from_shipment_id: uuid.UUID | None = None


class ItemNamed(BaseModel):
    """An item something was read from, as a line naming it shows it."""

    item_kind: ItemKind
    item_id: uuid.UUID
    # A file's name, a mail's subject or its writer, a bill's number.
    label: str | None
    # A mail's writer, by name or address.
    writer: str | None = None
    # When it came: a mail sent, a file's first mail, noon UTC on a bill's issue day.
    at: datetime | None = None


class PlateSource(ItemNamed):
    """Where code read one of a truck's plates."""

    field: Literal["truck_plate", "trailer_plate"]


class PlateSeen(BaseModel):
    """A plate a project's mail, papers or bills name, with each item naming it, oldest first,
    and the truck of the project it is, if any (a misread of its plate counts)."""

    project_id: uuid.UUID
    # As the oldest item wrote it.
    plate: str
    # What a label beside it said it is ("Dorse:"), where the items agree.
    role: Literal["truck", "trailer"] | None
    # The project's truck not rejected whose plates name it.
    shipment_id: uuid.UUID | None
    # Only a truck the owner rejected names it.
    rejected: bool
    items: list[ItemNamed]


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    truck_plate: str | None
    trailer_plate: str | None
    driver_name: str | None
    driver_phone: str | None
    subcontractor_id: uuid.UUID | None
    dispatcher_id: uuid.UUID | None
    location_contact: LocationContact | None
    loading_date: date | None
    delivery_date: date | None
    status: ShipmentStatus
    # Proposed by code from its plates, confirmed (the owner's, or one the owner made), rejected.
    state: ShipmentState
    # The facts a proposed truck was read from.
    evidence: list[uuid.UUID]
    field_sources: dict[str, FieldSource]
    # The newest reported position, if any.
    last_location: LocationOut | None
    # Where code read the plates it wrote; none for a plate the owner typed.
    sources: list[PlateSource] = []
    created_at: datetime
