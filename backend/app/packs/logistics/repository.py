import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.packs.logistics.models import Shipment, ShipmentLocation, ShipmentState
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
    db: AsyncSession,
    company_id: uuid.UUID,
    project_ids: Sequence[uuid.UUID],
    *,
    for_update: bool = False,
) -> Sequence[Shipment]:
    """With ``for_update``, read fresh and held until commit: an owner's edit of one of them waits,
    while a link may still name them (no key lock, so a link's foreign key check never waits)."""
    statement = (
        select(Shipment)
        .where(Shipment.company_id == company_id, Shipment.project_id.in_(project_ids))
        .order_by(Shipment.project_id, Shipment.created_at, Shipment.id)
    )
    if for_update:
        statement = statement.with_for_update(key_share=True).execution_options(
            populate_existing=True
        )
    rows = await db.scalars(statement)
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


async def locations_since(
    db: AsyncSession, company_id: uuid.UUID, since: datetime, *, limit: int
) -> Sequence[tuple[ShipmentLocation, Shipment]]:
    """Each position reported since then, with its truck, the newest first: never a truck the
    owner said is not the job's."""
    rows = await db.execute(
        select(ShipmentLocation, Shipment)
        .join(
            Shipment,
            (Shipment.company_id == ShipmentLocation.company_id)
            & (Shipment.id == ShipmentLocation.shipment_id),
        )
        .where(
            ShipmentLocation.company_id == company_id,
            ShipmentLocation.created_at >= since,
            Shipment.state != ShipmentState.REJECTED,
        )
        .order_by(ShipmentLocation.created_at.desc(), ShipmentLocation.id.desc())
        .limit(limit)
    )
    return rows.tuples().all()
