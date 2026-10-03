import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class ShipmentStatus(StrEnum):
    PLANNED = "planned"
    LOADING = "loading"
    ON_ROAD = "on_road"
    AT_BORDER = "at_border"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class ShipmentState(StrEnum):
    """Whether the truck is the owner's: one code proposed waits for a yes, and a rejected one
    is kept so its plates are never proposed on that project again (ADR-0021)."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class LocationContact(StrEnum):
    """Who is asked where the truck is: its dispatcher when it has one (who already talks to the
    driver), otherwise the driver (ADR-0011)."""

    DRIVER = "driver"
    DISPATCHER = "dispatcher"


class LocationSource(StrEnum):
    # The driver's or dispatcher's link page.
    LINK = "link"
    # A place named in a message ("Kapıkule'de bekliyoruz").
    MESSAGE = "message"
    # A row read from a dispatcher's spreadsheet or its photo.
    SHEET = "sheet"
    OWNER = "owner"


class Shipment(CompanyScoped, Base):
    """One truck of a project. Documents, positions and bills attach to it, so one truck's CMR can
    never clear another truck's bill (ADR-0010)."""

    __tablename__ = "shipments"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        # The target of project_links' (company_id, project_id, shipment_id) key.
        UniqueConstraint("company_id", "project_id", "id"),
        ForeignKeyConstraint(
            ["company_id", "project_id"], ["projects.company_id", "projects.id"], ondelete="CASCADE"
        ),
        # No ON DELETE: a party still named on a truck is not deleted from under it.
        ForeignKeyConstraint(
            ["company_id", "subcontractor_id"], ["parties.company_id", "parties.id"]
        ),
        ForeignKeyConstraint(["company_id", "dispatcher_id"], ["parties.company_id", "parties.id"]),
        CheckConstraint(
            "location_contact IS DISTINCT FROM 'dispatcher' OR dispatcher_id IS NOT NULL",
            name="shipments_dispatcher_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    truck_plate: Mapped[str | None] = mapped_column(String(20))
    trailer_plate: Mapped[str | None] = mapped_column(String(20))
    driver_name: Mapped[str | None] = mapped_column(String(200))
    driver_phone: Mapped[str | None] = mapped_column(String(40))
    subcontractor_id: Mapped[uuid.UUID | None]
    dispatcher_id: Mapped[uuid.UUID | None]
    location_contact: Mapped[LocationContact | None] = mapped_column(
        text_enum(LocationContact, "shipments_location_contact_check")
    )
    loading_date: Mapped[date | None]
    delivery_date: Mapped[date | None]
    status: Mapped[ShipmentStatus] = mapped_column(
        text_enum(ShipmentStatus, "shipments_status_check"), default=ShipmentStatus.PLANNED
    )
    state: Mapped[ShipmentState] = mapped_column(
        text_enum(ShipmentState, "shipments_state_check"),
        default=ShipmentState.CONFIRMED,
        server_default=ShipmentState.CONFIRMED.value,
    )
    # The facts a proposed truck was read from: its plates in the project's papers and mail.
    evidence: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default="{}"
    )
    # Field name → FieldSource, as on projects: the owner's values win.
    field_sources: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ShipmentLocation(CompanyScoped, Base):
    """A reported position, kept with its raw text. Coordinates only when a place resolved."""

    __tablename__ = "shipment_locations"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "shipment_id"],
            ["shipments.company_id", "shipments.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="shipment_locations_coordinates_check"
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="shipment_locations_range_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    shipment_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # The time the position was true, which the reporter or the message gives, in business time.
    reported_at: Mapped[datetime]
    source: Mapped[LocationSource] = mapped_column(
        text_enum(LocationSource, "shipment_locations_source_check")
    )
    # As written. It is data, never instructions (root AGENTS.md, invariant 3).
    raw_text: Mapped[str] = mapped_column(Text, default="")
    place: Mapped[str | None] = mapped_column(String(300))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    status: Mapped[ShipmentStatus | None] = mapped_column(
        text_enum(ShipmentStatus, "shipment_locations_status_check")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
