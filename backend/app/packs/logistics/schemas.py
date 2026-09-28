import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.projects.models import FieldSource
from app.packs.logistics.models import LocationContact, LocationSource, ShipmentStatus


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
    pass


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
    field_sources: dict[str, FieldSource]
    # The newest reported position, if any.
    last_location: LocationOut | None
    created_at: datetime
