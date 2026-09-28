import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.modules.identity.deps import ActorDep
from app.packs.logistics import service
from app.packs.logistics.schemas import (
    LocationCreate,
    LocationOut,
    LocationPage,
    ShipmentCreate,
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
