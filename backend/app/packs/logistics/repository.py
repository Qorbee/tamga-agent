import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packs.logistics.models import Shipment, ShipmentLocation
from app.platform.pagination import PageParams


async def add(db: AsyncSession, row: Shipment | ShipmentLocation) -> None:
    db.add(row)
    await db.flush()


async def get_shipment(
    db: AsyncSession, company_id: uuid.UUID, shipment_id: uuid.UUID, *, for_update: bool = False
) -> Shipment | None:
    statement = select(Shipment).where(
        Shipment.company_id == company_id, Shipment.id == shipment_id
    )
    if for_update:
        statement = statement.with_for_update()
    shipment: Shipment | None = await db.scalar(statement)
    return shipment


async def list_shipments(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> Sequence[Shipment]:
    rows = await db.scalars(
        select(Shipment)
        .where(Shipment.company_id == company_id, Shipment.project_id.in_(project_ids))
        .order_by(Shipment.project_id, Shipment.created_at, Shipment.id)
    )
    return rows.all()


async def last_locations(
    db: AsyncSession, company_id: uuid.UUID, shipment_ids: Sequence[uuid.UUID]
) -> Sequence[ShipmentLocation]:
    rows = await db.scalars(
        select(ShipmentLocation)
        .where(
            ShipmentLocation.company_id == company_id,
            ShipmentLocation.shipment_id.in_(shipment_ids),
        )
        .distinct(ShipmentLocation.shipment_id)
        .order_by(
            ShipmentLocation.shipment_id,
            ShipmentLocation.reported_at.desc(),
            ShipmentLocation.created_at.desc(),
        )
    )
    return rows.all()


async def list_locations(
    db: AsyncSession, company_id: uuid.UUID, shipment_id: uuid.UUID, page: PageParams
) -> tuple[Sequence[ShipmentLocation], int]:
    filters = (
        ShipmentLocation.company_id == company_id,
        ShipmentLocation.shipment_id == shipment_id,
    )
    total = await db.scalar(select(func.count()).select_from(ShipmentLocation).where(*filters))
    rows = await db.scalars(
        select(ShipmentLocation)
        .where(*filters)
        .order_by(ShipmentLocation.reported_at.desc(), ShipmentLocation.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0
