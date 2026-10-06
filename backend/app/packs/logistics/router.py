import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.modules.identity.deps import ActorDep
from app.packs.logistics import filing, service
from app.packs.logistics.schemas import (
    LocationCreate,
    LocationOut,
    LocationPage,
    PlateSeen,
    ShipmentCreate,
    ShipmentHitch,
    ShipmentOut,
    ShipmentUpdate,
)
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

shipments = APIRouter(tags=["shipments"])


@shipments.get("/shipments")
async def list_shipments(
    actor: ActorDep,
    db: SessionDep,
    project_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=200)],
) -> list[ShipmentOut]:
    return await service.list_shipments(db, actor, project_id)


@shipments.get("/plates")
async def list_plates(
    actor: ActorDep,
    db: SessionDep,
    project_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=200)],
) -> list[PlateSeen]:
    """The plates these projects' mail, papers and bills name, with where each was read and the
    truck it is, if any."""
    return await filing.plates_seen(db, actor.company_id, project_id)


@shipments.post("/shipments", status_code=status.HTTP_201_CREATED)
async def create_shipment(body: ShipmentCreate, actor: ActorDep, db: SessionDep) -> ShipmentOut:
    values = body.model_dump(exclude_unset=True, exclude={"project_id"})
    return await service.create_shipment(db, actor, project_id=body.project_id, values=values)


@shipments.get("/shipments/{shipment_id}")
async def get_shipment(shipment_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ShipmentOut:
    return await service.get_shipment(db, actor, shipment_id)


@shipments.patch("/shipments/{shipment_id}")
async def update_shipment(
    shipment_id: uuid.UUID, body: ShipmentUpdate, actor: ActorDep, db: SessionDep
) -> ShipmentOut:
    # Only the fields sent: an explicit null clears one, an omitted one is left alone.
    return await service.update_shipment(
        db, actor, shipment_id, body.model_dump(exclude_unset=True)
    )


@shipments.post("/shipments/{shipment_id}/split", status_code=status.HTTP_201_CREATED)
async def split_shipment(shipment_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ShipmentOut:
    """Makes the truck's trailer a truck of its own; returns the new truck."""
    return await service.split_shipment(db, actor, shipment_id)


@shipments.post("/shipments/{shipment_id}/hitch")
async def hitch_shipment(
    shipment_id: uuid.UUID, body: ShipmentHitch, actor: ActorDep, db: SessionDep
) -> ShipmentOut:
    """Makes another truck's lone plate, or a plate seen on the job, this truck's trailer; 409
    when a truck or the plate changed since it was shown."""
    if body.from_shipment_id is not None:
        return await service.hitch_shipment(
            db, actor, shipment_id, body.from_shipment_id, body.plate
        )
    return await service.hitch_plate(db, actor, shipment_id, body.plate)


@shipments.post("/shipments/{shipment_id}/swap")
async def swap_plates(shipment_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ShipmentOut:
    """Swaps the truck's plates as they stand; 409 when it has not both."""
    return await service.swap_plates(db, actor, shipment_id)


@shipments.get("/shipments/{shipment_id}/locations")
async def list_locations(
    shipment_id: uuid.UUID, actor: ActorDep, db: SessionDep, page: PageDep
) -> LocationPage:
    return await service.list_locations(db, actor, shipment_id, page)


@shipments.post("/shipments/{shipment_id}/locations", status_code=status.HTTP_201_CREATED)
async def add_location(
    shipment_id: uuid.UUID, body: LocationCreate, actor: ActorDep, db: SessionDep
) -> LocationOut:
    return await service.add_location(db, actor, shipment_id, body)
