"""Shipments: one per truck, with who is asked where it is, and the positions reported for it
(docs/architecture/09, ADR-0010). The logistics pack's own tables; the core reaches them only
through what this pack registers.

Every editable field records its source through the projects module's provenance rules, so an
extraction never overwrites what the owner typed.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from enum import Enum
from typing import Any, Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.conversations import service as conversations
from app.modules.documents import service as documents
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.modules.parties import service as parties
from app.modules.projects import checklist
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource, LinkMethod, LinkStatus, ProposalTarget
from app.packs.logistics import repository
from app.packs.logistics.models import (
    LocationContact,
    LocationSource,
    Shipment,
    ShipmentLocation,
    ShipmentState,
)
from app.packs.logistics.schemas import (
    LocationCreate,
    LocationOut,
    LocationPage,
    PlateSource,
    ShipmentFields,
    ShipmentOut,
)
from app.platform import clock
from app.platform.auth import Actor, Role
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.items import ItemKind
from app.platform.pagination import PageParams

_EDITORS: Final = (Role.OWNER, Role.OPS)
_FIELDS: Final = frozenset(ShipmentFields.model_fields)
_PLATES: Final[tuple[Literal["truck_plate", "trailer_plate"], ...]] = (
    "truck_plate",
    "trailer_plate",
)


async def refuse_merge(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """A project with a truck the owner confirmed, wrote on, marked or followed is not merged into
    another: the truck, and the papers confirmed on it, are tied to it in the database. The owner
    merges the other way. What code proposed goes: its papers come off its trucks, a proposed
    truck is dropped (the papers that named it propose it again on the project that stays), and
    a rejected one stays rejected there, so its plates stay out."""
    trucks = await repository.list_shipments(db, company_id, [source_id], for_update=True)
    if await _touched(db, company_id, trucks):
        raise Conflict("This project has trucks: merge the other project into this one")
    await projects.take_off_trucks(db, company_id, [truck.id for truck in trucks])
    target = list(await repository.list_shipments(db, company_id, [target_id], for_update=True))
    for truck in trucks:
        if truck.state is ShipmentState.REJECTED:
            await _keep_rejected(db, company_id, truck, target_id, target)
        await projects.drop_field_proposals(db, company_id, ProposalTarget.SHIPMENT, truck.id)
        await db.delete(truck)
    await db.flush()


async def drop_untouched(db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID) -> int:
    """Drops the trucks code proposed on a project and the owner has had no hand in, for its
    papers to propose them again under the rules of today; how many went. A rejected truck stays,
    so its plates stay out. Does not commit."""
    trucks = await repository.list_shipments(db, company_id, [project_id], for_update=True)
    proposed = [truck for truck in trucks if truck.state is ShipmentState.PROPOSED]
    touched = await _touched(db, company_id, proposed)
    dropped = [truck for truck in proposed if truck.id not in touched]
    await projects.take_off_trucks(db, company_id, [truck.id for truck in dropped])
    for truck in dropped:
        await projects.drop_field_proposals(db, company_id, ProposalTarget.SHIPMENT, truck.id)
        await db.delete(truck)
    await db.flush()
    return len(dropped)


async def _touched(
    db: AsyncSession, company_id: uuid.UUID, trucks: Sequence[Shipment]
) -> set[uuid.UUID]:
    """The trucks the owner has had a hand in: confirmed, written on, marked, followed, or
    carrying a paper the owner placed on it."""
    ids = [truck.id for truck in trucks]
    followed = {
        location.shipment_id for location in await repository.last_locations(db, company_id, ids)
    }
    placed = {
        link.shipment_id
        for link in await projects.links_on_trucks(db, company_id, ids)
        if link.method is LinkMethod.OWNER and link.shipment_id is not None
    }
    answered = {
        truck.id
        for truck in trucks
        if truck.state is ShipmentState.CONFIRMED or _written_by_owner(truck)
    }
    return answered | followed | placed | await checklist.marked_trucks(db, company_id, ids)


def _written_by_owner(truck: Shipment) -> bool:
    return any(FieldSource(source) is FieldSource.OWNER for source in truck.field_sources.values())


async def _keep_rejected(
    db: AsyncSession,
    company_id: uuid.UUID,
    rejected: Shipment,
    target_id: uuid.UUID,
    target: list[Shipment],
) -> None:
    """Carries the owner's rejection of a truck to the project a merge keeps: a truck code
    proposed there with the same plates is rejected with it, and where no truck there names all
    of them, the rejected one is made again."""
    plates = compared_plates(rejected)
    same = [truck for truck in target if compared_plates(truck) & plates]
    proposed = [truck for truck in same if truck.state is ShipmentState.PROPOSED]
    await projects.take_off_trucks(db, company_id, [truck.id for truck in proposed])
    for truck in proposed:
        truck.state = ShipmentState.REJECTED
    if plates <= {plate for truck in same for plate in compared_plates(truck)}:
        return
    again = await add_shipment(db, company_id=company_id, project_id=target_id)
    again.state, again.evidence = ShipmentState.REJECTED, list(rejected.evidence)
    again.truck_plate, again.trailer_plate = rejected.truck_plate, rejected.trailer_plate
    again.field_sources = dict(rejected.field_sources)
    target.append(again)


def compared_plates(truck: Shipment) -> set[str]:
    """A truck's plates as facts compare them."""
    return {extractors.reference(p) for p in (truck.truck_plate, truck.trailer_plate) if p}


async def create_shipment(
    db: AsyncSession, actor: Actor, *, project_id: uuid.UUID, values: Mapping[str, Any]
) -> ShipmentOut:
    actor.require(*_EDITORS)
    if not await projects.project_exists(db, actor.company_id, project_id):
        raise NotFound("No such project", field="project_id")
    shipment = await add_shipment(db, company_id=actor.company_id, project_id=project_id)
    await _write(db, shipment, values, FieldSource.OWNER, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [shipment]))[0]


async def add_shipment(
    db: AsyncSession, *, company_id: uuid.UUID, project_id: uuid.UUID
) -> Shipment:
    """An empty truck on a project ("10 tır" makes ten); does not commit."""
    shipment = Shipment(company_id=company_id, project_id=project_id, field_sources={})
    await repository.add(db, shipment)
    return shipment


async def fill_shipment(
    db: AsyncSession, shipment: Shipment, values: Mapping[str, Any], *, source: FieldSource
) -> None:
    """Writes a truck's fields from code (a sandbox's scenario, an import), as ``source`` says
    they came; does not commit."""
    await _write(db, shipment, values, source, evidence=())


async def update_shipment(
    db: AsyncSession, actor: Actor, shipment_id: uuid.UUID, values: Mapping[str, Any]
) -> ShipmentOut:
    """The owner's edit of a truck, and the owner's answer to one code proposed: a rejected
    truck loses the papers code placed on it, and keeps its plates from being proposed again."""
    actor.require(*_EDITORS)
    state = values.get("state")
    if state is not None:
        # Whether a truck is the job's is the owner's call, as a link's is.
        actor.require(Role.OWNER)
    shipment = await _shipment(db, actor.company_id, shipment_id)
    fields = {field: value for field, value in values.items() if field != "state"}
    if state is not None:
        if state == ShipmentState.REJECTED:
            await projects.take_off_trucks(db, actor.company_id, [shipment.id])
        shipment.state = ShipmentState(state)
    await _write(db, shipment, fields, FieldSource.OWNER, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [shipment]))[0]


async def split_shipment(db: AsyncSession, actor: Actor, shipment_id: uuid.UUID) -> ShipmentOut:
    """The owner's word that a truck's trailer is a truck of its own: it leaves the truck and
    becomes another of the job, with the papers it was read from, in one step."""
    actor.require(*_EDITORS)
    truck = await _shipment(db, actor.company_id, shipment_id)
    plate = truck.trailer_plate
    if truck.state is ShipmentState.REJECTED or not truck.truck_plate or plate is None:
        raise Conflict("This truck has no trailer to make a truck of")
    other = await add_shipment(db, company_id=actor.company_id, project_id=truck.project_id)
    other.evidence = await _read_as(db, actor.company_id, truck.evidence, plate)
    await _write(db, other, {"truck_plate": plate}, FieldSource.OWNER, evidence=())
    await _write(db, truck, {"trailer_plate": None}, FieldSource.OWNER, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [other]))[0]


async def swap_plates(db: AsyncSession, actor: Actor, shipment_id: uuid.UUID) -> ShipmentOut:
    """The truck's two plates the other way round, as they stand when it is locked: a plate
    another tab wrote meanwhile is swapped, never written back over."""
    actor.require(*_EDITORS)
    truck = await _shipment(db, actor.company_id, shipment_id)
    if not truck.truck_plate or not truck.trailer_plate:
        raise Conflict("This truck has no trailer to swap with")
    swapped = {"truck_plate": truck.trailer_plate, "trailer_plate": truck.truck_plate}
    await _write(db, truck, swapped, FieldSource.OWNER, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [truck]))[0]


async def hitch_shipment(
    db: AsyncSession, actor: Actor, shipment_id: uuid.UUID, lone_id: uuid.UUID, shown: str
) -> ShipmentOut:
    """The owner's word that a lone plate on the job is another truck's trailer: the truck takes
    it, and the row it stood on is no truck of the job. Refused when either changed since the
    board showed them: the truck took a trailer, the plate is another than ``shown`` or got a
    trailer, or one was rejected."""
    actor.require(Role.OWNER)
    if lone_id == shipment_id:
        raise BadRequest("A truck cannot pull itself", field="from_shipment_id")
    # Locked in the order of their ids: two such steps on one pair never wait on each other.
    first, second = sorted((shipment_id, lone_id))
    locked = {
        first: await _shipment(db, actor.company_id, first),
        second: await _shipment(db, actor.company_id, second),
    }
    truck, lone = locked[shipment_id], locked[lone_id]
    plate = lone.truck_plate
    if (
        truck.project_id != lone.project_id
        or ShipmentState.REJECTED in (truck.state, lone.state)
        or not truck.truck_plate
        or truck.trailer_plate is not None
        or plate is None
        or extractors.reference(plate) != extractors.reference(shown)
        or lone.trailer_plate is not None
    ):
        raise Conflict("These trucks changed since they were shown")
    truck.evidence = sorted(
        {*truck.evidence, *await _read_as(db, actor.company_id, lone.evidence, plate)}
    )
    await _write(db, truck, {"trailer_plate": plate}, FieldSource.OWNER, evidence=())
    await projects.take_off_trucks(db, actor.company_id, [lone.id])
    lone.state = ShipmentState.REJECTED
    await db.commit()
    return (await _describe(db, actor.company_id, [truck]))[0]


async def _read_as(
    db: AsyncSession, company_id: uuid.UUID, evidence: Sequence[uuid.UUID], plate: str
) -> list[uuid.UUID]:
    """The facts among a truck's evidence that read this plate: where it was read goes with it."""
    found = await facts.get_facts(db, company_id, list(evidence))
    wanted = extractors.reference(plate)
    return sorted(fact_id for fact_id, fact in found.items() if fact.normalised == wanted)


async def propose_shipment_fields(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    shipment_id: uuid.UUID,
    values: Mapping[str, Any],
    source: FieldSource,
    evidence: Sequence[uuid.UUID],
) -> None:
    """What an extraction read about a truck: written where nothing stronger holds the field,
    proposed to the owner where it does. Does not commit."""
    if source is FieldSource.OWNER:
        raise ValueError("an extraction or a suggestion, never the owner")
    shipment = await _shipment(db, company_id, shipment_id)
    await _write(db, shipment, values, source, evidence)


async def list_shipments(
    db: AsyncSession, actor: Actor, project_ids: Sequence[uuid.UUID]
) -> list[ShipmentOut]:
    rows = await repository.list_shipments(db, actor.company_id, project_ids)
    return await _describe(db, actor.company_id, rows)


async def get_shipment(db: AsyncSession, actor: Actor, shipment_id: uuid.UUID) -> ShipmentOut:
    shipment = await _shipment(db, actor.company_id, shipment_id, lock=False)
    return (await _describe(db, actor.company_id, [shipment]))[0]


async def add_location(
    db: AsyncSession, actor: Actor, shipment_id: uuid.UUID, body: LocationCreate
) -> LocationOut:
    """A position the owner types in. The link pages and the extractors add theirs with their own
    source (Oct 3)."""
    actor.require(*_EDITORS)
    await _shipment(db, actor.company_id, shipment_id, lock=False)
    if (body.latitude is None) != (body.longitude is None):
        raise BadRequest("Give both latitude and longitude, or neither", field="latitude")
    if not (body.place or body.raw_text or body.latitude is not None):
        raise BadRequest("Say where the truck is", field="place")
    company = (await companies.get_companies(db, [actor.company_id]))[actor.company_id]
    location = ShipmentLocation(
        company_id=actor.company_id,
        shipment_id=shipment_id,
        reported_at=body.reported_at or clock.now(company),
        source=LocationSource.OWNER,
        raw_text=body.raw_text,
        place=body.place,
        latitude=body.latitude,
        longitude=body.longitude,
        status=body.status,
    )
    await repository.add(db, location)
    await db.commit()
    return LocationOut.model_validate(location)


async def list_locations(
    db: AsyncSession, actor: Actor, shipment_id: uuid.UUID, page: PageParams
) -> LocationPage:
    await _shipment(db, actor.company_id, shipment_id, lock=False)
    rows, total = await repository.list_locations(db, actor.company_id, shipment_id, page)
    return LocationPage(
        items=[LocationOut.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def accept_field(
    db: AsyncSession, company_id: uuid.UUID, shipment_id: uuid.UUID, field: str, value: Any
) -> None:
    """Writes an accepted field proposal as the owner's (registered with the projects module)."""
    if field not in _FIELDS:
        raise BadRequest(f"{field} is not an editable shipment field")
    parsed = ShipmentFields.model_validate({field: value})
    shipment = await _shipment(db, company_id, shipment_id)
    await _write(db, shipment, {field: getattr(parsed, field)}, FieldSource.OWNER, evidence=())


async def _write(
    db: AsyncSession,
    shipment: Shipment,
    values: Mapping[str, Any],
    source: FieldSource,
    evidence: Sequence[uuid.UUID],
) -> None:
    unknown = set(values) - _FIELDS
    if unknown:
        raise BadRequest(f"Not a shipment field: {', '.join(sorted(unknown))}")
    cleaned = {field: _clean(field, value) for field, value in values.items()}
    await _check_parties(db, shipment.company_id, cleaned)
    writes, _ = await projects.apply_fields(
        db,
        company_id=shipment.company_id,
        target=ProposalTarget.SHIPMENT,
        row=shipment,
        values={field: _as_json(value) for field, value in cleaned.items()},
        source=source,
        evidence=evidence,
    )
    for field in writes:
        setattr(shipment, field, cleaned[field])
    if shipment.location_contact is LocationContact.DISPATCHER and shipment.dispatcher_id is None:
        raise BadRequest("Name the dispatcher who is asked for this truck", field="dispatcher_id")
    await db.flush()


def _clean(field: str, value: Any) -> Any:
    if isinstance(value, str) and not isinstance(value, Enum):
        value = value.strip() or None
        if field in _PLATES and value is not None:
            value = " ".join(value.upper().split())
    return value


def _as_json(value: Any) -> Any:
    if isinstance(value, date | uuid.UUID):
        return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
    return value


async def _check_parties(
    db: AsyncSession, company_id: uuid.UUID, values: Mapping[str, Any]
) -> None:
    ids = [values[f] for f in ("subcontractor_id", "dispatcher_id") if values.get(f) is not None]
    if not ids:
        return
    found = await parties.get_parties(db, company_id, ids)
    for field in ("subcontractor_id", "dispatcher_id"):
        if values.get(field) is not None and values[field] not in found:
            raise NotFound("No such party", field=field)


async def _shipment(
    db: AsyncSession, company_id: uuid.UUID, shipment_id: uuid.UUID, *, lock: bool = True
) -> Shipment:
    shipment = await repository.get_shipment(db, company_id, shipment_id, for_update=lock)
    if shipment is None:
        raise NotFound("No such shipment")
    return shipment


async def _describe(
    db: AsyncSession, company_id: uuid.UUID, shipments: Sequence[Shipment]
) -> list[ShipmentOut]:
    last = {
        location.shipment_id: location
        for location in await repository.last_locations(db, company_id, [s.id for s in shipments])
    }
    sources = await _plate_sources(db, company_id, shipments)
    return [
        ShipmentOut(
            id=s.id,
            project_id=s.project_id,
            truck_plate=s.truck_plate,
            trailer_plate=s.trailer_plate,
            driver_name=s.driver_name,
            driver_phone=s.driver_phone,
            subcontractor_id=s.subcontractor_id,
            dispatcher_id=s.dispatcher_id,
            location_contact=s.location_contact,
            loading_date=s.loading_date,
            delivery_date=s.delivery_date,
            status=s.status,
            state=s.state,
            evidence=s.evidence,
            field_sources={k: FieldSource(v) for k, v in s.field_sources.items()},
            last_location=LocationOut.model_validate(last[s.id]) if s.id in last else None,
            sources=sources.get(s.id, []),
            created_at=s.created_at,
        )
        for s in shipments
    ]


async def _plate_sources(
    db: AsyncSession, company_id: uuid.UUID, shipments: Sequence[Shipment]
) -> dict[uuid.UUID, list[PlateSource]]:
    """For each plate code wrote on a truck, the item among its evidence that names it."""
    found = await facts.get_facts(
        db, company_id, sorted({fact_id for s in shipments for fact_id in s.evidence})
    )
    read: dict[uuid.UUID, list[tuple[Literal["truck_plate", "trailer_plate"], Any]]] = {}
    for s in shipments:
        for field in _PLATES:
            # An owner's plate shows a paper only where the truck's evidence reads that very
            # plate: one the owner moved (a split, a trailer) keeps where it was read.
            plate = getattr(s, field)
            if not plate:
                continue
            fact = next(
                (
                    found[fact_id]
                    for fact_id in s.evidence
                    if fact_id in found and found[fact_id].normalised == extractors.reference(plate)
                ),
                None,
            )
            if fact is not None:
                read.setdefault(s.id, []).append((field, fact))
    items = [fact for pairs in read.values() for _, fact in pairs]
    labels = await _labels(db, company_id, items)
    return {
        truck_id: [
            PlateSource(
                field=field,
                item_kind=fact.item_kind,
                item_id=fact.item_id,
                label=labels.get((fact.item_kind, fact.item_id)),
            )
            for field, fact in pairs
        ]
        for truck_id, pairs in read.items()
    }


async def _labels(
    db: AsyncSession, company_id: uuid.UUID, found: Sequence[Any]
) -> dict[tuple[ItemKind, uuid.UUID], str | None]:
    """Each item's name as a list shows it: a file's name, a mail's subject or writer, a bill's
    number."""
    ids: dict[ItemKind, list[uuid.UUID]] = {}
    for fact in found:
        ids.setdefault(fact.item_kind, []).append(fact.item_id)
    labels: dict[tuple[ItemKind, uuid.UUID], str | None] = {}
    files = await documents.filenames(db, company_id, ids.get(ItemKind.DOCUMENT, []))
    labels |= {(ItemKind.DOCUMENT, i): name for i, name in files.items()}
    mails = await conversations.mail_labels(db, company_id, ids.get(ItemKind.MESSAGE, []))
    labels |= {(ItemKind.MESSAGE, i): mail.subject or mail.writer for i, mail in mails.items()}
    numbers = await books.invoice_numbers(db, company_id, ids.get(ItemKind.INVOICE, []))
    labels |= {(ItemKind.INVOICE, i): number for i, number in numbers.items()}
    return labels


async def proofs(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Each confirmed truck's proofs of delivery that count for money (ADR-0021, decision 4): a
    confirmed link the owner made or answered, marked as proof, on that truck. The ids of the
    links, by truck; a truck with none is left out. The payment rules read only this."""
    trucks = [
        truck.id
        for truck in await repository.list_shipments(db, company_id, project_ids)
        if truck.state is ShipmentState.CONFIRMED
    ]
    found: dict[uuid.UUID, list[uuid.UUID]] = {}
    for link in await projects.links_on_trucks(db, company_id, trucks):
        if (
            link.status is LinkStatus.CONFIRMED
            and link.proof_of_delivery
            and link.method is LinkMethod.OWNER
            and link.shipment_id is not None
        ):
            found.setdefault(link.shipment_id, []).append(link.id)
    return found


async def delivered(db: AsyncSession, company_id: uuid.UUID, invoice_id: uuid.UUID) -> list[bool]:
    """For the payment rules: each truck a bill pays for, and whether its delivery is proven by
    proof that counts for money (ADR-0021, decision 4). Empty when the bill names no truck."""
    trucks = await trucks_billed(db, company_id, invoice_id)
    if not trucks:
        return []
    jobs = {
        link.project_id
        for link in await projects.links_of_items(db, company_id, ItemKind.INVOICE, [invoice_id])
        if projects.is_evidence(link)
    }
    proven = await proofs(db, company_id, sorted(jobs))
    return [truck in proven for truck in trucks]


async def trucks_billed(
    db: AsyncSession, company_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[uuid.UUID]:
    """The confirmed trucks a bill pays for, in the projects that hold it as evidence (confirmed,
    or named by a key surely enough, ADR-0010): the truck its link names, else those whose plates
    the bill names. None when it names none, and the payment rules then hold the bill."""
    links = [
        link
        for link in await projects.links_of_items(db, company_id, ItemKind.INVOICE, [invoice_id])
        if projects.is_evidence(link)
    ]
    trucks = {
        truck.id: truck
        for truck in await repository.list_shipments(
            db, company_id, list({link.project_id for link in links})
        )
        if truck.state is ShipmentState.CONFIRMED
    }
    # The bill as booked, and the e-invoice it was booked from, whose plates filing read too.
    records = [invoice_id, *await books.booked_from(db, company_id, [invoice_id])]
    named = {
        fact.normalised
        for fact in await facts.facts_of_items(
            db, company_id=company_id, item_kind=ItemKind.INVOICE, item_ids=records
        )
        if fact.kind == "plate"
    }
    billed: list[uuid.UUID] = []
    for link in links:
        if link.shipment_id is not None:
            if link.shipment_id in trucks:
                billed.append(link.shipment_id)
            continue
        for truck in trucks.values():
            plates = {
                extractors.reference(p) for p in (truck.truck_plate, truck.trailer_plate) if p
            }
            if truck.project_id == link.project_id and plates & named:
                billed.append(truck.id)
    return sorted(set(billed))
