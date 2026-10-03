"""Keeping a project's file by code (ADR-0021): the trucks its papers and mail name, proposed for
the owner's yes; the truck each item belongs to, where only one fits; and the bills a plate ties
to a project. Code proposes and suggests; it never confirms a truck and never marks a proof.

A plate is no key: one truck carries many jobs. So a truck is proposed only from what is the
job's own (a truck paper, a purchase bill, or two of its items), and a bill is placed by plate only
when one project fits its plate within the days of the job.
"""

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceKind
from app.modules.conversations import service as conversations
from app.modules.documents import service as documents
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.modules.facts.models import Fact
from app.modules.projects import service as projects
from app.modules.projects.models import (
    FieldSource,
    LinkMethod,
    Project,
    ProjectLink,
)
from app.packs.logistics import repository
from app.packs.logistics.models import Shipment, ShipmentState
from app.packs.logistics.service import add_shipment, fill_shipment
from app.platform import events
from app.platform.db import open_session
from app.platform.items import ItemKind

PLATE: Final = "plate"
# The papers that travel with one truck: a plate on one of them is that truck's.
TRUCK_PAPERS: Final = frozenset({"cmr", "pod_photo", "loading_photo", "e_irsaliye"})
# An item naming more plates than this is a fleet list or a rate sheet, no one job's trucks.
MAX_PLATES: Final = 4
# A bill is placed by plate when issued from this long before the job's first day to this long
# after its last: a carrier bills after delivery, sometimes weeks after.
BILLED_BEFORE: Final = timedelta(days=10)
BILLED_AFTER: Final = timedelta(days=60)
_PLATE_CONFIDENCE: Final = 0.7


@dataclass(slots=True)
class Filed:
    trucks: int = 0
    # Links given the one truck their plates name.
    placed: int = 0
    # Bills placed in a project by plate.
    bills: int = 0


async def file_all() -> Filed:
    """Files every company's projects, each company in a transaction of its own."""
    async with open_session() as db:
        company_ids = await projects.companies_with_projects(db)
    total = Filed()
    for company_id in company_ids:
        async with open_session() as db:
            filed = await file_company(db, company_id)
            if filed.trucks or filed.placed or filed.bills:
                await events.publish(db, company_id, events.EventKind.PROJECTS)
            await db.commit()
        total.trucks += filed.trucks
        total.placed += filed.placed
        total.bills += filed.bills
    return total


async def file_company(db: AsyncSession, company_id: uuid.UUID) -> Filed:
    """Proposes trucks, places items on them, then bills by plate. Does not commit."""
    filed = Filed()
    open_projects = await projects.open_projects(db, company_id)
    for project in open_projects:
        links = await projects.links_of_project(db, company_id, project.id)
        plates = await _plates(db, company_id, links)
        # Locked: the owner's answer to a truck waits for this run rather than being overwritten.
        trucks = list(
            await repository.list_shipments(db, company_id, [project.id], for_update=True)
        )
        new = await _propose_trucks(db, project, links, plates, trucks)
        filed.trucks += len(new)
        filed.placed += await _place_on_trucks(db, company_id, links, plates, [*trucks, *new])
    filed.bills = await _bills_by_plate(db, company_id, open_projects)
    await db.flush()
    return filed


@dataclass(frozen=True, slots=True)
class _Plates:
    """The plates each item names, as compared and as written, with the facts that say so."""

    of: dict[tuple[ItemKind, uuid.UUID], dict[str, Fact]]


async def _plates(db: AsyncSession, company_id: uuid.UUID, links: Sequence[ProjectLink]) -> _Plates:
    of: dict[tuple[ItemKind, uuid.UUID], dict[str, Fact]] = {}
    for kind in (ItemKind.MESSAGE, ItemKind.DOCUMENT, ItemKind.INVOICE):
        ids = [link.item_id for link in links if link.item_kind is kind]
        for fact in await facts.facts_of_items(
            db, company_id=company_id, item_kind=kind, item_ids=ids
        ):
            if fact.kind == PLATE:
                of.setdefault((kind, fact.item_id), {})[fact.normalised] = fact
    # A fleet list names no one job's trucks.
    return _Plates({item: found for item, found in of.items() if len(found) <= MAX_PLATES})


async def _propose_trucks(
    db: AsyncSession,
    project: Project,
    links: Sequence[ProjectLink],
    plates: _Plates,
    trucks: Sequence[Shipment],
) -> list[Shipment]:
    """A truck for each plate a truck paper or a purchase bill names, or two of the project's
    items do, with its trailer when items naming exactly two plates pair them most often. A
    plate on a truck the project has, in any state, is never proposed again: a rejected truck
    keeps its plates out. A plate paired with a truck the project has is that truck's trailer,
    no truck of its own."""
    papers = await _truck_papers(db, project.company_id, links)
    seen: Counter[str] = Counter()
    sure: set[str] = set()
    pairs: Counter[frozenset[str]] = Counter()
    written: dict[str, Fact] = {}
    for item, found in plates.of.items():
        seen.update(found.keys())
        written.update(found)
        if item in papers:
            sure.update(found)
        if len(found) == 2:
            pairs[frozenset(found)] += 1
    known = {_compared(plate) for truck in trucks for plate in _plates_of(truck)}
    pulling = {_compared(truck.truck_plate): truck for truck in trucks if truck.truck_plate}
    wanted = [
        plate
        for plate, count in seen.most_common()
        if (plate in sure or count >= 2) and plate not in known
    ]
    proposed: list[Shipment] = []
    taken: set[str] = set()
    for plate in wanted:
        if plate in taken:
            continue
        trailer = _partner(plate, pairs, taken)
        if trailer in known:
            # The other plate is a truck the project has: this one is its trailer.
            taken.add(plate)
            await _hitch(db, pulling.get(trailer), written[plate])
            continue
        group = [plate] if trailer is None else [plate, trailer]
        taken.update(group)
        truck = await add_shipment(db, company_id=project.company_id, project_id=project.id)
        truck.state = ShipmentState.PROPOSED
        truck.evidence = sorted(written[each].id for each in group)
        values = {"truck_plate": written[plate].value}
        if trailer is not None:
            values["trailer_plate"] = written[trailer].value
        await fill_shipment(db, truck, values, source=FieldSource.EXTRACTION)
        proposed.append(truck)
    return proposed


async def _hitch(db: AsyncSession, truck: Shipment | None, trailer: Fact) -> None:
    """Names a truck's trailer when a paper pairs the two and the truck has none yet."""
    if truck is None or truck.trailer_plate or truck.state is ShipmentState.REJECTED:
        return
    truck.evidence = sorted({*truck.evidence, trailer.id})
    await fill_shipment(db, truck, {"trailer_plate": trailer.value}, source=FieldSource.EXTRACTION)


def _partner(plate: str, pairs: Counter[frozenset[str]], taken: set[str]) -> str | None:
    """The plate an item naming exactly two pairs with this one most often; none on a tie."""
    with_it = [(pair, count) for pair, count in pairs.items() if plate in pair]
    with_it = [(pair, count) for pair, count in with_it if not (pair - {plate}) & taken]
    if not with_it:
        return None
    best = max(count for _, count in with_it)
    top = [pair for pair, count in with_it if count == best]
    if len(top) > 1:
        return None
    [other] = top[0] - {plate}
    return other


async def _truck_papers(
    db: AsyncSession, company_id: uuid.UUID, links: Sequence[ProjectLink]
) -> set[tuple[ItemKind, uuid.UUID]]:
    """The project's items that are one truck's: its truck papers and its purchase bills."""
    files = [link.item_id for link in links if link.item_kind is ItemKind.DOCUMENT]
    bills = [link.item_id for link in links if link.item_kind is ItemKind.INVOICE]
    typed = await documents.get_documents(db, company_id, files)
    papers = {
        (ItemKind.DOCUMENT, file.id) for file in typed.values() if file.doc_type in TRUCK_PAPERS
    }
    for bill in await books.get_invoices(db, company_id, bills):
        if bill.kind in (InvoiceKind.PURCHASE, InvoiceKind.INCOMING_E_INVOICE):
            papers.add((ItemKind.INVOICE, bill.id))
    return papers


async def _place_on_trucks(
    db: AsyncSession,
    company_id: uuid.UUID,
    links: Sequence[ProjectLink],
    plates: _Plates,
    trucks: Sequence[Shipment],
) -> int:
    """Gives a link the one truck not rejected whose plates its item names; the owner's links,
    and links already on a truck, are left as they are."""
    live = [truck for truck in trucks if truck.state is not ShipmentState.REJECTED]
    placed = 0
    for link in links:
        if link.method is LinkMethod.OWNER or link.shipment_id is not None:
            continue
        named = set(plates.of.get((link.item_kind, link.item_id), {}))
        if not named:
            continue
        fits = [truck for truck in live if {_compared(p) for p in _plates_of(truck)} & named]
        if len(fits) == 1 and await projects.suggest_truck(db, company_id, link.id, fits[0].id):
            placed += 1
    return placed


async def _bills_by_plate(
    db: AsyncSession, company_id: uuid.UUID, open_projects: Sequence[Project]
) -> int:
    """Suggests a bill no project holds to the one project with a truck, not rejected, whose
    plate the bill names, when the bill was issued within that job's days."""
    bills = {bill.id: bill for bill in await books.placeable_invoices(db, company_id)}
    # Placed once, as the record a project holds (an e-invoice as its booked purchase).
    targets = {bill.id: bill.same_as_id or bill.id for bill in bills.values()}
    held = {
        link.item_id
        for link in await projects.links_of_items(db, company_id, ItemKind.INVOICE, list(bills))
    }
    loose_records = {
        each for each, target in targets.items() if target in bills and not held & {each, target}
    }
    if not loose_records:
        return 0
    named: dict[uuid.UUID, dict[str, Fact]] = {}
    for fact in await facts.facts_of_items(
        db, company_id=company_id, item_kind=ItemKind.INVOICE, item_ids=list(loose_records)
    ):
        if fact.kind == PLATE:
            named.setdefault(targets[fact.item_id], {})[fact.normalised] = fact
    trucks = await repository.list_shipments(db, company_id, [p.id for p in open_projects])
    by_plate: dict[str, set[uuid.UUID]] = {}
    for truck in trucks:
        if truck.state is not ShipmentState.REJECTED:
            for plate in _plates_of(truck):
                by_plate.setdefault(_compared(plate), set()).add(truck.project_id)
    days = await _days(db, company_id, open_projects)
    placed = 0
    for target, found in named.items():
        issued = bills[target].issue_date
        if issued is None or len(found) > MAX_PLATES:
            continue
        fits = {
            project_id
            for plate in found
            for project_id in by_plate.get(plate, set())
            if _within(issued, days.get(project_id))
        }
        if len(fits) != 1:
            continue
        [project_id] = fits
        await projects.link_item(
            db,
            company_id=company_id,
            item_kind=ItemKind.INVOICE,
            item_id=target,
            project_id=project_id,
            method=LinkMethod.SCORED,
            confidence=_PLATE_CONFIDENCE,
            evidence=sorted(fact.id for fact in found.values()),
        )
        placed += 1
    return placed


async def _days(
    db: AsyncSession, company_id: uuid.UUID, open_projects: Sequence[Project]
) -> dict[uuid.UUID, tuple[date, date]]:
    """Each project's days: its own first and last day, else those of its mail."""
    spans: dict[uuid.UUID, tuple[date, date]] = {}
    for project in open_projects:
        if project.starts_on and project.ends_on:
            spans[project.id] = (project.starts_on, project.ends_on)
            continue
        links = await projects.links_of_project(db, company_id, project.id)
        mails = await conversations.message_summaries(
            db, company_id, [link.item_id for link in links if link.item_kind is ItemKind.MESSAGE]
        )
        sent = sorted(mail.sent_at.date() for mail in mails.values())
        first = project.starts_on or (sent[0] if sent else None)
        last = project.ends_on or (sent[-1] if sent else None)
        if first and last:
            spans[project.id] = (first, last)
    return spans


def _within(issued: date, span: tuple[date, date] | None) -> bool:
    return span is not None and span[0] - BILLED_BEFORE <= issued <= span[1] + BILLED_AFTER


def _plates_of(truck: Shipment) -> list[str]:
    return [plate for plate in (truck.truck_plate, truck.trailer_plate) if plate]


def _compared(plate: str) -> str:
    return extractors.reference(plate)
