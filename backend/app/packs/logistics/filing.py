"""Keeping a project's file by code (ADR-0021): the trucks its own papers name, proposed for the
owner's yes; the truck each item belongs to, where only one fits; and the bills a plate ties to a
project. Code proposes and suggests; it never confirms a truck and never marks a proof.

A plate is no key, and a mail is no evidence of a truck: one truck carries many jobs, and a mail
names trucks that were offered, swapped or meant for another load. So a truck is proposed only
from the job's own papers (a consignment note, a dispatch note, a loading or delivery photo, a
carrier's bill placed by something other than its plate), dated within the job's days, and no
more of them than the job's trucks when its conversations say how many. Two plates are a truck
and its trailer only where a paper says so: a consignment or dispatch note is written for one
vehicle, the truck first, and a label ("Dorse:", "Trailer:") names a trailer anywhere. Two plates
written once apart from each other ("both trucks, 06 ABC 101 and 06 XYZ 202") stay two.
"""

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceKind
from app.modules.books.schemas import InvoiceOut
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
from app.packs.logistics import repository, service
from app.packs.logistics.facts import PLATE, TRAILER_ROLE, TRUCK_ROLE
from app.packs.logistics.models import Shipment, ShipmentState
from app.packs.logistics.schemas import PlateSeen
from app.packs.logistics.service import (
    MAX_PLATES,
    JobPlates,
    add_shipment,
    drop_untouched,
    fill_shipment,
    job_plates,
)
from app.platform import events
from app.platform.db import open_session
from app.platform.items import ItemKind

# The papers that travel with one truck: a plate on one of them is that truck's.
TRUCK_PAPERS: Final = frozenset({"cmr", "pod_photo", "loading_photo", "e_irsaliye"})
# Written for one vehicle: two plates on one are its truck and its trailer, the truck first.
ONE_VEHICLE_PAPERS: Final = frozenset({"cmr", "e_irsaliye"})
# A paper is the job's when dated from this long before its first day to this long after its
# last: a note is written days before loading, a photo or a bill comes after delivery.
PAPER_BEFORE: Final = timedelta(days=7)
PAPER_AFTER: Final = timedelta(days=21)
# A bill is placed by plate when issued from this long before the job's first day to this long
# after its last: a carrier bills after delivery, sometimes weeks after.
BILLED_BEFORE: Final = timedelta(days=10)
BILLED_AFTER: Final = timedelta(days=60)
_PLATE_CONFIDENCE: Final = 0.7
# What a label beside a plate makes it, as a plate seen is shown.
_ROLES: Final[dict[str, Literal["truck", "trailer"]]] = {
    TRUCK_ROLE: "truck",
    TRAILER_ROLE: "trailer",
}
# Characters a reading mistakes for each other: one of them in place of the other is a misread.
_LOOKALIKES: Final = frozenset(
    frozenset(pair) for pair in ("0O", "0D", "1I", "2Z", "4A", "5S", "6G", "8B")
)

type _Item = tuple[ItemKind, uuid.UUID]


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
    expected = await _expected(db, company_id, open_projects)
    for project in open_projects:
        new, placed = await file_trucks(db, project, expected.get(project.id))
        filed.trucks += len(new)
        filed.placed += placed
    filed.bills = await _bills_by_plate(db, company_id, open_projects)
    await db.flush()
    return filed


async def file_trucks(
    db: AsyncSession, project: Project, expected: int | None
) -> tuple[list[Shipment], int]:
    """One project's trucks proposed from its papers, no more than the ``expected`` trucks of its
    job where they are known, and its items placed on them: the trucks proposed, and how many
    links were placed. Does not commit."""
    company_id = project.company_id
    links = await projects.links_of_project(db, company_id, project.id)
    plates = await job_plates(db, company_id, links)
    # Locked: the owner's answer to a truck waits for this run rather than being overwritten.
    trucks = list(await repository.list_shipments(db, company_id, [project.id], for_update=True))
    new = await _propose_trucks(db, project, links, plates, trucks, expected)
    placed = await _place_on_trucks(db, company_id, links, plates, [*trucks, *new])
    return new, placed


@dataclass(frozen=True, slots=True)
class Refiled:
    """One project's trucks, not rejected, before and after it was filed again."""

    project_id: uuid.UUID
    number: str
    expected: int | None
    before: int
    dropped: int
    proposed: int

    @property
    def after(self) -> int:
        return self.before - self.dropped + self.proposed


async def refile_company(db: AsyncSession, company_id: uuid.UUID) -> list[Refiled]:
    """Every project's trucks proposed again under the rules of today: those code proposed and the
    owner has had no hand in are dropped, then the project's papers propose them again and its
    items are placed on them. Bills stay where they are. Does not commit."""
    open_projects = await projects.open_projects(db, company_id)
    expected = await _expected(db, company_id, open_projects)
    refiled: list[Refiled] = []
    for project in open_projects:
        trucks = await repository.list_shipments(db, company_id, [project.id])
        before = sum(truck.state is not ShipmentState.REJECTED for truck in trucks)
        dropped = await drop_untouched(db, company_id, project.id)
        new, _ = await file_trucks(db, project, expected.get(project.id))
        refiled.append(
            Refiled(project.id, project.number, expected.get(project.id), before, dropped, len(new))
        )
    await db.flush()
    return refiled


async def _expected(
    db: AsyncSession, company_id: uuid.UUID, open_projects: Sequence[Project]
) -> dict[uuid.UUID, int]:
    """How many trucks each job has, where its conversations say."""
    jobs = await projects.jobs_of(db, company_id, [project.id for project in open_projects])
    return {project_id: job.trucks for project_id, job in jobs.items() if job.trucks is not None}


@dataclass(frozen=True, slots=True)
class _Paper:
    item: _Item
    day: date | None
    # A consignment or dispatch note: its two plates are one vehicle.
    one_vehicle: bool = False
    text: str = ""


@dataclass(slots=True)
class _Seen:
    """A truck the papers name: the papers, the trailers they pair it with, its first day."""

    papers: set[_Item] = field(default_factory=set)
    trailers: Counter[str] = field(default_factory=Counter)
    first: date | None = None


async def _propose_trucks(
    db: AsyncSession,
    project: Project,
    links: Sequence[ProjectLink],
    plates: JobPlates,
    trucks: Sequence[Shipment],
    expected: int | None,
) -> list[Shipment]:
    """The trucks the project's papers name and it does not have yet, each with its trailer where
    a paper pairs them, the best shown first and no more than the job's trucks. A plate on a
    truck the project has, in any state, is never proposed again: a rejected truck keeps its
    plates out, and a misread of them. A plate a paper pairs with a truck the project has is
    that truck's trailer."""
    seen, trailers, written, together = _vehicles(await _papers(db, project, links), plates)
    known = {_compared(plate) for truck in trucks for plate in _plates_of(truck)}

    def is_known(plate: str) -> bool:
        return any(_same_plate(plate, each, together) for each in known)

    pulling = {_compared(truck.truck_plate): truck for truck in trucks if truck.truck_plate}
    for plate, vehicle in seen.items():
        pulled = [truck for kept, truck in pulling.items() if _same_plate(plate, kept, together)]
        if len(pulled) == 1 and vehicle.trailers:
            trailer = _most(vehicle.trailers)
            if trailer is not None and not is_known(trailer):
                await _hitch(db, pulled[0], written[trailer])
                known.add(trailer)

    def best_shown(plate: str) -> tuple[int, int, str]:
        # The most papers first, then the one nearest the first day of loading.
        first, start = seen[plate].first, project.starts_on
        return -len(seen[plate].papers), abs((first - start).days) if first and start else 0, plate

    wanted = sorted(
        (plate for plate in seen if not is_known(plate) and plate not in trailers), key=best_shown
    )
    live = sum(truck.state is not ShipmentState.REJECTED for truck in trucks)
    if expected is not None:
        wanted = wanted[: max(expected - live, 0)]
    proposed: list[Shipment] = []
    for plate in wanted:
        trailer = _most(seen[plate].trailers)
        if trailer is not None and is_known(trailer):
            trailer = None
        group = [plate] if trailer is None else [plate, trailer]
        known.update(group)
        truck = await add_shipment(db, company_id=project.company_id, project_id=project.id)
        truck.state = ShipmentState.PROPOSED
        # Every paper that named its plates, so the board shows each.
        truck.evidence = sorted(
            {written[each].id for each in group}
            | {
                fact.id
                for item in seen[plate].papers
                for each, fact in plates.of.get(item, {}).items()
                if each in group
            }
        )
        values = {"truck_plate": written[plate].value}
        if trailer is not None:
            values["trailer_plate"] = written[trailer].value
        await fill_shipment(db, truck, values, source=FieldSource.EXTRACTION)
        proposed.append(truck)
    return proposed


def _vehicles(
    papers: Sequence[_Paper], plates: JobPlates
) -> tuple[dict[str, _Seen], set[str], dict[str, Fact], set[frozenset[str]]]:
    """What the papers say of the job's vehicles: each truck's plate with what names it, every
    plate a paper or a label shows is a trailer, each plate as written, and the plates one paper
    names together. Misreads of one plate are one plate."""
    seen: dict[str, _Seen] = {}
    trailers = {
        plate
        for roles in plates.roles.values()
        for plate, role in roles.items()
        if role == TRAILER_ROLE
    }
    written: dict[str, Fact] = {}
    together: set[frozenset[str]] = set()
    for paper in papers:
        found = plates.of.get(paper.item, {})
        written.update(found)
        together.update(frozenset({a, b}) for a in found for b in found if a != b)
        pair = _pair(paper, found, plates.roles.get(paper.item, {}))
        units = [pair] if pair else [(plate, None) for plate in found]
        for truck, trailer in units:
            vehicle = seen.setdefault(truck, _Seen())
            vehicle.papers.add(paper.item)
            if paper.day and (vehicle.first is None or paper.day < vehicle.first):
                vehicle.first = paper.day
            if trailer is not None:
                vehicle.trailers[trailer] += 1
                trailers.add(trailer)
    same = _misreads(seen, together)
    merged: dict[str, _Seen] = {}
    for plate, vehicle in seen.items():
        into = merged.setdefault(same[plate], _Seen())
        into.papers |= vehicle.papers
        into.trailers.update({same.get(t, t): n for t, n in vehicle.trailers.items()})
        if vehicle.first and (into.first is None or vehicle.first < into.first):
            into.first = vehicle.first
    return merged, {same.get(plate, plate) for plate in trailers}, written, together


def _pair(paper: _Paper, found: dict[str, Fact], roles: dict[str, str]) -> tuple[str, str] | None:
    """The truck and trailer one paper names together, where it says which is which: a label on
    one of two plates, or a consignment or dispatch note's two plates, the truck written first."""
    if len(found) != 2:
        return None
    first, second = sorted(found, key=lambda plate: _at(paper.text, found[plate]))
    if roles.get(first) == TRAILER_ROLE or roles.get(second) == TRUCK_ROLE:
        first, second = second, first
    if TRAILER_ROLE in (roles.get(first), roles.get(second)) or paper.one_vehicle:
        return first, second
    return None


def _at(text: str, plate: Fact) -> int:
    """Where a plate is first written in a paper's text; a plate not found there comes last."""
    at = text.find(plate.value)
    return at if at >= 0 else len(text)


def _misreads(seen: dict[str, _Seen], together: set[frozenset[str]]) -> dict[str, str]:
    """Each plate's one form among its misreads ("06 KLM 345" read as "06 KML 345"): the one the
    most papers name. Two plates one paper names together are two vehicles, never a misread."""
    plates = sorted(seen, key=lambda plate: (-len(seen[plate].papers), plate))
    same: dict[str, str] = {}
    for plate in plates:
        same[plate] = next(
            (
                kept
                for kept in dict.fromkeys(same.values())
                if _misread(plate, kept) and frozenset({plate, kept}) not in together
            ),
            plate,
        )
    return same


def _same_plate(a: str, b: str, together: set[frozenset[str]]) -> bool:
    """Whether two plates are one: the same, or one a misread of the other that no paper names
    beside it."""
    return a == b or (_misread(a, b) and frozenset({a, b}) not in together)


def _misread(a: str, b: str) -> bool:
    """Whether two plates are one read twice: the same length and the same first two characters,
    and one character read as a lookalike, or two neighbours swapped."""
    if len(a) != len(b) or a[:2] != b[:2] or a == b:
        return False
    differ = [i for i in range(len(a)) if a[i] != b[i]]
    if len(differ) == 1:
        return frozenset({a[differ[0]], b[differ[0]]}) in _LOOKALIKES
    first, second = differ[0], differ[-1]
    return (
        len(differ) == 2 and second == first + 1 and (a[first], a[second]) == (b[second], b[first])
    )


def _most(trailers: Counter[str]) -> str | None:
    """The trailer the papers pair a truck with most often; none on a tie."""
    ranked = trailers.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


async def _hitch(db: AsyncSession, truck: Shipment | None, trailer: Fact) -> None:
    """Names a truck's trailer when a paper pairs the two and the truck has none yet."""
    if truck is None or truck.trailer_plate or truck.state is ShipmentState.REJECTED:
        return
    truck.evidence = sorted({*truck.evidence, trailer.id})
    await fill_shipment(db, truck, {"trailer_plate": trailer.value}, source=FieldSource.EXTRACTION)


async def plates_seen(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> list[PlateSeen]:
    """The plates each project's mail, papers and bills name, oldest first, each with the items
    naming it and the project's truck it is (a misread counts). A fleet list names none. What
    code does not propose as a truck (a plate only mail named, one past the job's count) is
    still shown, for the owner to judge."""
    links = await projects.links_of_projects(db, company_id, project_ids)
    found = await job_plates(db, company_id, links)
    projects_of: dict[_Item, set[uuid.UUID]] = {}
    for link in links:
        projects_of.setdefault((link.item_kind, link.item_id), set()).add(link.project_id)
    named = await service.named_items(db, company_id, list(found.of))
    trucks: dict[uuid.UUID, list[Shipment]] = {}
    for truck in await repository.list_shipments(db, company_id, project_ids):
        trucks.setdefault(truck.project_id, []).append(truck)
    seen: dict[tuple[uuid.UUID, str], list[tuple[_Item, Fact]]] = {}
    # Two plates one of a project's items names together are two vehicles, however alike.
    together: dict[uuid.UUID, set[frozenset[str]]] = {}
    for item, plates in found.of.items():
        pairs = {frozenset({a, b}) for a in plates for b in plates if a != b}
        for project_id in projects_of.get(item, ()):
            together.setdefault(project_id, set()).update(pairs)
            for plate, fact in plates.items():
                seen.setdefault((project_id, plate), []).append((item, fact))
    shown = []
    for (project_id, plate), where in seen.items():
        where = sorted(
            (each for each in where if each[0] in named),
            key=lambda each: (named[each[0]].at or datetime.max.replace(tzinfo=UTC), each[0][1]),
        )
        if not where:
            continue
        holding = [
            truck
            for truck in trucks.get(project_id, [])
            if any(
                _same_plate(plate, _compared(each), together.get(project_id, set()))
                for each in _plates_of(truck)
            )
        ]
        live = [truck for truck in holding if truck.state is not ShipmentState.REJECTED]
        roles = {role for item, _ in where if (role := found.roles.get(item, {}).get(plate))}
        shown.append(
            PlateSeen(
                project_id=project_id,
                plate=where[0][1].value,
                role=_ROLES.get(roles.pop()) if len(roles) == 1 else None,
                shipment_id=live[0].id if live else None,
                rejected=bool(holding) and not live,
                items=[named[item] for item, _ in where],
            )
        )
    return sorted(shown, key=lambda each: each.items[0].at or datetime.max.replace(tzinfo=UTC))


async def _papers(db: AsyncSession, project: Project, links: Sequence[ProjectLink]) -> list[_Paper]:
    """The project's items that are one truck's, dated within the job's days: its truck papers,
    and its carriers' bills placed by something other than a plate (a bill placed by its plate
    cannot vouch for that plate's truck)."""
    company_id = project.company_id
    files = [link.item_id for link in links if link.item_kind is ItemKind.DOCUMENT]
    typed = await documents.get_documents(db, company_id, files)
    truck_files = [file for file in typed.values() if file.doc_type in TRUCK_PAPERS]
    carried = await conversations.first_carriers(db, company_id, [file.id for file in truck_files])
    papers = [
        _Paper(
            item=(ItemKind.DOCUMENT, file.id),
            day=(carried[file.id].sent_at if file.id in carried else file.created_at).date(),
            one_vehicle=file.doc_type in ONE_VEHICLE_PAPERS,
            text=file.text,
        )
        for file in truck_files
    ]
    by_plate = {
        link.item_id
        for link in links
        if link.item_kind is ItemKind.INVOICE and link.method is LinkMethod.SCORED
    }
    bills = [
        link.item_id
        for link in links
        if link.item_kind is ItemKind.INVOICE and link.item_id not in by_plate
    ]
    for bill in await books.get_invoices(db, company_id, bills):
        if bill.kind in (InvoiceKind.PURCHASE, InvoiceKind.INCOMING_E_INVOICE):
            papers.append(_Paper(item=(ItemKind.INVOICE, bill.id), day=bill.issue_date))
    return [paper for paper in papers if _of_the_job(paper.day, project)]


def _of_the_job(day: date | None, project: Project) -> bool:
    """Whether a paper's day falls within the job's days, as far as they are known. A paper with
    no day keeps it, as does a job with none: the day cannot speak against it."""
    if day is None:
        return True
    first, last = project.starts_on, project.ends_on or project.starts_on
    return (first is None or first - PAPER_BEFORE <= day) and (
        last is None or day <= last + PAPER_AFTER
    )


async def _place_on_trucks(
    db: AsyncSession,
    company_id: uuid.UUID,
    links: Sequence[ProjectLink],
    plates: JobPlates,
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
        # A paper that misread a truck's plate is that truck's too.
        fits = [
            truck
            for truck in live
            if any(_same_plate(a, _compared(b), set()) for a in named for b in _plates_of(truck))
        ]
        if len(fits) == 1 and await projects.suggest_truck(db, company_id, link.id, fits[0].id):
            placed += 1
    return placed


async def _bills_by_plate(
    db: AsyncSession, company_id: uuid.UUID, open_projects: Sequence[Project]
) -> int:
    """Suggests a bill no project holds to the one project with a truck, not rejected, whose
    plate the bill names, when the bill was issued within that job's days and its firm is the
    project's in the bill's role. A plate alone is no key: one truck carries many clients'
    loads, most of them no project in Tamga, so the one project with the plate is no proof."""
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
    firms = await projects.firms_of(db, company_id, [project.id for project in open_projects])
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
            and _firm_agrees(firms[project_id], bills[target])
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


async def recheck_bills_by_plate(db: AsyncSession, company_id: uuid.UUID) -> int:
    """Removes the bills code placed by plate in a project whose firm in the bill's role is not
    the bill's, or is not known, as the rule of today would not place them; a link the owner
    answered stays. How many went. Does not commit."""
    links = await projects.suggested_links(db, company_id, ItemKind.INVOICE, LinkMethod.SCORED)
    bills = {
        bill.id: bill
        for bill in await books.get_invoices(db, company_id, [link.item_id for link in links])
    }
    firms = await projects.firms_of(db, company_id, list({link.project_id for link in links}))
    wrong = [
        link.id
        for link in links
        if link.item_id not in bills
        or not _firm_agrees(firms[link.project_id], bills[link.item_id])
    ]
    return await projects.drop_suggested(db, company_id, wrong)


def _firm_agrees(firms: projects.ProjectFirms, bill: InvoiceOut) -> bool:
    billing = firms.billing(bill.kind)
    return billing is not None and billing.agree(bill.party_id, bill.party_name) is True


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
