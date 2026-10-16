"""What the logistics pack gives the assistant: trucks named by their plates in a project's file,
and the owner's answer to a truck Tamga proposed, or a truck of their own, asked for in the chat.
Both go through the pack's service, as the project page's do."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic_ai import RunContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant import tools
from app.modules.assistant.news import News
from app.modules.assistant.seat import Seat
from app.modules.linking import service as linking
from app.packs.logistics import repository, service
from app.packs.logistics.models import Shipment, ShipmentState
from app.platform.db import open_session
from app.platform.errors import DomainError


async def name_trucks(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Mapping[uuid.UUID, str]:
    """A project's trucks, a handful: read one by one."""
    named = {}
    for truck_id in ids:
        truck = await repository.get_shipment(db, company_id, truck_id)
        if truck is not None:
            named[truck.id] = _line(truck)
    return named


def _line(truck: Shipment) -> str:
    plates = " + ".join(plate for plate in (truck.truck_plate, truck.trailer_plate) if plate)
    parts = [plates or "no plate yet"]
    if truck.state is ShipmentState.PROPOSED:
        parts.append("proposed by Tamga, waiting for the owner")
    elif truck.state is ShipmentState.REJECTED:
        parts.append("the owner said it is not this job's")
    if truck.driver_name:
        parts.append(f"driver {truck.driver_name}")
    if truck.status:
        parts.append(str(truck.status))
    return ", ".join(parts)


@tools.packs.tool
async def answer_truck(
    context: RunContext[Seat], truck: int, answer: Literal["confirmed", "rejected"]
) -> str:
    """Confirms a truck Tamga proposed (t1, from a project opened) as the job's, or rejects it: a
    rejected truck loses the papers Tamga put on it, and its plates are not proposed again."""
    seat = context.deps
    truck_id = seat.look("t", truck)
    if truck_id is None:
        return "No such truck: use a handle that open returned."
    async with open_session() as db:
        try:
            await service.update_shipment(db, seat.actor, truck_id, {"state": answer})
        except DomainError as error:
            return tools.refused(error)
    return f"t{truck} is {answer} now."


@tools.packs.tool
async def add_truck(
    context: RunContext[Seat],
    project: str,
    plate: str,
    trailer: str | None = None,
    driver: str | None = None,
) -> str:
    """Adds a truck to a project, by the project's number: its plate, and its trailer's and its
    driver's name when the owner gave them."""
    seat = context.deps
    project_id = seat.projects.get(linking.bare(project))
    if project_id is None:
        return "No such project: use a number that a tool returned."
    values = {"truck_plate": plate, "trailer_plate": trailer, "driver_name": driver}
    async with open_session() as db:
        try:
            made = await service.create_shipment(
                db,
                seat.actor,
                project_id=project_id,
                values={field: value for field, value in values.items() if value},
            )
        except DomainError as error:
            return tools.refused(error)
    return f"Added {seat.name('t', made.id)} to {project}: {made.truck_plate}."


async def positions(db: AsyncSession, company_id: uuid.UUID, since: datetime) -> list[News]:
    """Where a project's trucks were reported since then, as news on the project: the plate,
    the place as it was given, and how far the truck is."""
    found = []
    for location, truck in await repository.locations_since(db, company_id, since, limit=200):
        plates = " + ".join(plate for plate in (truck.truck_plate, truck.trailer_plate) if plate)
        where = location.place or " ".join(location.raw_text.split())[:120] or "a position"
        status = f", {location.status.value.replace('_', ' ')}" if location.status else ""
        found.append(
            News(
                id=location.id,
                project_id=truck.project_id,
                at=location.created_at,
                true_at=location.reported_at,
                kind="truck",
                # The card names this truck, and opens its project.
                opens=truck.id,
                line=f"Truck {plates or 'with no plate yet'}: {where}{status}",
                # A truck's reports in one message are one line: its latest.
                topic=str(truck.id),
            )
        )
    return found
