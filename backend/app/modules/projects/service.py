"""Projects: the files a broker's work is rebuilt into, and the evidence tying items to them
(docs/architecture/09, ADR-0010).

- A project's number is drawn in the company's own format, and every number it ever had stays its
  alias: a number once used is never given to another project.
- Every editable field records who set it. The owner's value is never overwritten by an
  extraction or a suggestion; that value waits as a field proposal until the owner decides.
- A link carries its method, confidence, the facts it cites and who decided it. Only a confirmed
  link is proof for money, and a proof of delivery confirms at most one truck.
- Two projects that turn out to be one job are merged by the owner: one stays, and holds
  everything the other had.

Operations called by other modules and packs (``link_item``, ``apply_fields``) run in the caller's
transaction and never commit; those behind the API commit.
"""

import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import ColumnElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.conversations import service as conversations
from app.modules.conversations.models import Channel
from app.modules.documents import service as documents
from app.modules.documents.schemas import Holder
from app.modules.parties import service as parties
from app.modules.projects import fields, numbering, repository
from app.modules.projects.models import (
    FieldProposal,
    FieldSource,
    LinkMethod,
    LinkStatus,
    Project,
    ProjectLink,
    ProjectNumber,
    ProjectParty,
    ProjectRole,
    ProjectStatus,
    ProposalStatus,
    ProposalTarget,
)
from app.modules.projects.schemas import (
    DocumentBrief,
    InvoiceBrief,
    ItemBrief,
    LinkOut,
    LinkPage,
    MessageBrief,
    NumberingOut,
    ProjectJob,
    ProjectOut,
    ProjectPage,
    ProjectPartyOut,
    ProjectSort,
    ProposalOut,
    ProposalPage,
)
from app.platform import clock
from app.platform.auth import Actor, Role
from app.platform.db import violated_constraint
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.items import ItemKind
from app.platform.pagination import PageParams

# The owner's moves on a project's status (docs/architecture/09, States).
_TRANSITIONS: Final[dict[ProjectStatus, frozenset[ProjectStatus]]] = {
    ProjectStatus.DRAFT: frozenset({ProjectStatus.ACTIVE}),
    ProjectStatus.PROPOSED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.REJECTED}),
    ProjectStatus.ACTIVE: frozenset({ProjectStatus.CLOSED}),
}
_EDITORS: Final = (Role.OWNER, Role.OPS)
_NUMBER_TAKEN: Final = "project_numbers_company_id_normalised_key"
_ONE_PROOF: Final = "project_links_one_confirmed_proof"
_SHIPMENT_KEY: Final = "project_links_company_id_project_id_shipment_id_fkey"
_SORTS: Final[dict[str, ColumnElement[Any]]] = {
    "number": Project.number.asc(),
    "-number": Project.number.desc(),
    "name": Project.name.asc(),
    "-name": Project.name.desc(),
    "starts_on": Project.starts_on.asc().nulls_last(),
    "-starts_on": Project.starts_on.desc().nulls_last(),
    "created_at": Project.created_at.asc(),
    "-created_at": Project.created_at.desc(),
    "updated_at": Project.updated_at.asc(),
    "-updated_at": Project.updated_at.desc(),
}
# Editable project fields; stage is computed, status moves only through _TRANSITIONS.
_DATES: Final = ("starts_on", "ends_on")
_NOON: Final = time(12)
_PROJECT_FIELDS: Final = frozenset({"name", "number", *_DATES})


# Asked before a project is merged into another, and told once its links have moved, both in
# the merge's own transaction: the session, the company, the project that goes and the one that
# stays. A guard that keeps rows which cannot follow raises ``Conflict``.
type MergeHook = Callable[[AsyncSession, uuid.UUID, uuid.UUID, uuid.UUID], Awaitable[None]]
_merge_guards: list[MergeHook] = []
_merge_listeners: list[MergeHook] = []
# Reads what a module knows of each project's job, for the projects shown: the session, the
# company and the projects. The linker registers the one that reads conversations.
type JobReader = Callable[
    [AsyncSession, uuid.UUID, Sequence[uuid.UUID]], Awaitable[Mapping[uuid.UUID, ProjectJob]]
]
_job_readers: list[JobReader] = []
# Which of two links to one item says more: the owner's yes, then a suggestion, then a no.
_LINK_RANK: Final = {LinkStatus.REJECTED: 0, LinkStatus.SUGGESTED: 1, LinkStatus.CONFIRMED: 2}
_CONFIRMED_BY_OWNER: Final = (ProjectStatus.ACTIVE, ProjectStatus.CLOSED)
# How many mails' files are carried in one step of a backfill.
_CARRY_BATCH: Final = 200


def guard_merge(guard: MergeHook) -> None:
    """A module or pack that keeps rows under a project which cannot follow it into another
    refuses the merge, before anything is changed."""
    _merge_guards.append(guard)


def on_merge(listener: MergeHook) -> None:
    """A module that keeps rows naming a project moves them when it is merged into another: the
    project that goes is deleted next, and what still names it goes with it."""
    _merge_listeners.append(listener)


def read_jobs_with(reader: JobReader) -> None:
    """A module that reads what a project's job is fills the project's ``job`` for every read."""
    _job_readers.append(reader)


class NumberTaken(Conflict):
    code = "number_taken"


# Projects


async def create_project(
    db: AsyncSession, actor: Actor, *, name: str, number: str | None
) -> ProjectOut:
    """The owner (or ops) opening a project on the board: a draft, until confirmed or linked."""
    actor.require(*_EDITORS)
    project = await new_project(
        db,
        company_id=actor.company_id,
        name=name,
        number=number,
        status=ProjectStatus.DRAFT,
        source=FieldSource.OWNER,
    )
    await db.commit()
    return (await _describe(db, actor.company_id, [project]))[0]


async def new_project(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    name: str,
    status: ProjectStatus,
    source: FieldSource,
    number: str | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
) -> Project:
    """A project with its number drawn or checked. For the board, the assistant's tools and the
    linker's proposals; does not commit."""
    if status not in (ProjectStatus.DRAFT, ProjectStatus.PROPOSED):
        raise ValueError("a project starts as a draft or a proposal")
    name = name.strip()
    if not name:
        raise BadRequest("Name the project", field="name")
    chosen = number.strip() if number else await _draw_number(db, company_id)
    project = Project(
        company_id=company_id,
        number=chosen,
        name=name,
        status=status,
        starts_on=starts_on,
        ends_on=ends_on,
        field_sources={
            "name": source,
            "number": source if number else FieldSource.SUGGESTION,
            **{
                field: source
                for field, day in zip(_DATES, (starts_on, ends_on), strict=True)
                if day
            },
        },
    )
    await repository.add(db, project)
    await _claim_number(db, company_id, project.id, chosen)
    return project


async def list_projects(
    db: AsyncSession,
    actor: Actor,
    *,
    statuses: Sequence[ProjectStatus],
    q: str | None,
    suggested: bool | None,
    sort: ProjectSort,
    page: PageParams,
) -> ProjectPage:
    rows, total = await repository.list_projects(
        db,
        actor.company_id,
        statuses=statuses,
        q=q.strip() if q else None,
        suggested=suggested,
        order_by=[_SORTS[sort]],
        page=page,
    )
    return ProjectPage(
        items=await _describe(db, actor.company_id, rows),
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def get_project(db: AsyncSession, actor: Actor, project_id: uuid.UUID) -> ProjectOut:
    project = await _project(db, actor.company_id, project_id)
    return (await _describe(db, actor.company_id, [project]))[0]


async def update_project(
    db: AsyncSession,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    name: str | None,
    number: str | None,
    dates: Mapping[str, date | None],
    status: ProjectStatus | None,
) -> ProjectOut:
    """``dates`` holds only the dates the request named: null clears one."""
    actor.require(*_EDITORS)
    project = await _project(db, actor.company_id, project_id, for_update=True)
    if status is not None and status is not project.status:
        actor.require(Role.OWNER)
        if status not in _TRANSITIONS.get(project.status, frozenset()):
            raise BadRequest(f"A {project.status} project cannot become {status}", field="status")
        project.status = status
        if status is ProjectStatus.REJECTED:
            # Its mails stay as answered, but a rejected project takes no files (ADR-0021).
            await repository.drop_project_mail_links(db, actor.company_id, project.id)
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if number is not None:
        values["number"] = number
    values.update(dates)
    await _write_project_fields(db, project, values, FieldSource.OWNER, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [project]))[0]


async def merge_project(
    db: AsyncSession, actor: Actor, project_id: uuid.UUID, *, into: uuid.UUID
) -> ProjectOut:
    """The owner saying two projects are one job: ``project_id`` goes into ``into``, which keeps
    its own name, number and days. Its links move (where both tie the same item, the one that
    says more stays: a confirmation, then a suggestion, then a rejection), its numbers become
    numbers of the project that stays, so a paper that carries one still finds the job, and its
    parties join the other's; two different prices for one party in one role are refused, for
    the owner to settle first. A project the owner had confirmed makes the one that stays
    active. The project that goes is then deleted: this is not undone."""
    actor.require(Role.OWNER)
    if project_id == into:
        raise BadRequest("Choose another project to merge into", field="into")
    # Locked in one order, so two merges of the same pair cannot wait on each other.
    locked = {
        each: await repository.get_project(db, actor.company_id, each, for_update=True)
        for each in sorted((project_id, into))
    }
    source, target = locked[project_id], locked[into]
    if source is None:
        raise NotFound("No such project")
    if target is None:
        raise NotFound("No such project", field="into")
    if ProjectStatus.REJECTED in (source.status, target.status):
        raise BadRequest("A rejected project is not merged", field="into")
    for guard in _merge_guards:
        await guard(db, actor.company_id, source.id, target.id)
    await _move_parties(db, actor.company_id, source.id, target.id)
    await _move_links(db, actor.company_id, source.id, target.id)
    await repository.move_numbers(db, actor.company_id, source.id, target.id)
    await repository.drop_proposals(db, actor.company_id, ProposalTarget.PROJECT, source.id)
    if source.status in _CONFIRMED_BY_OWNER and target.status not in _CONFIRMED_BY_OWNER:
        target.status = ProjectStatus.ACTIVE
    await db.flush()
    for listener in _merge_listeners:
        await listener(db, actor.company_id, source.id, target.id)
    mails = [
        link.item_id
        for link in await repository.all_links_of_project(db, actor.company_id, target.id)
        if link.item_kind is ItemKind.MESSAGE
    ]
    await carry_files(db, actor.company_id, mails)
    await repository.delete(db, source)
    await db.commit()
    return (await _describe(db, actor.company_id, [target]))[0]


async def _move_parties(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """A project's parties to the one that stays. A party both hold in the same role stays
    once, with the price either gave it; two prices that differ are the owner's to choose
    between, so nothing is merged."""
    there = {
        (row.party_id, row.role): row
        for row in await repository.list_project_parties(db, company_id, [target_id])
    }
    for row in await repository.list_project_parties(db, company_id, [source_id]):
        other = there.get((row.party_id, row.role))
        if other is None:
            row.project_id = target_id
        elif other.amount is None:
            other.amount, other.currency = row.amount, row.currency
        elif row.amount is not None and (row.amount, row.currency) != (
            other.amount,
            other.currency,
        ):
            raise Conflict(
                "Both projects hold a different price for the same party: change one first",
                field="into",
            )
    await db.flush()


async def _move_links(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """A project's links to the one that stays. Where both tie the same item one link stays:
    the owner's (ADR-0021), else the one that says more, whole, with the truck it names and
    whether it is that truck's proof of delivery. A truck never comes from the project that goes
    (its guard refuses one with trucks), and what the owner did not confirm of a truck is not
    confirmed by a merge."""
    held = {
        (link.item_kind, link.item_id): link
        for link in await repository.all_links_of_project(db, company_id, target_id)
    }
    for link in await repository.all_links_of_project(db, company_id, source_id):
        other = held.get((link.item_kind, link.item_id))
        if other is None:
            link.project_id = target_id
            continue
        if _weight(link) > _weight(other):
            other.status, other.method = link.status, link.method
            other.decided_by, other.decided_at = link.decided_by, link.decided_at
            other.shipment_id, other.proof_of_delivery = None, False
        other.confidence = max(other.confidence, link.confidence)
        other.evidence = sorted(set(other.evidence) | set(link.evidence))
        await db.delete(link)
    await db.flush()


def _weight(link: ProjectLink) -> tuple[bool, int]:
    """Which of two links to one item stays in a merge: the owner's, then the one that says more."""
    return link.method is LinkMethod.OWNER, _LINK_RANK[link.status]


async def project_exists(db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    return await repository.get_project(db, company_id, project_id) is not None


async def get_projects(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Project]:
    return {p.id: p for p in await repository.get_projects(db, company_id, ids)}


async def find_projects(
    db: AsyncSession, company_id: uuid.UUID, *, q: str | None, limit: int
) -> Sequence[Project]:
    """Projects by a word of their name or by a number they have had, most recently changed
    first, without those the owner rejected."""
    rows, _ = await repository.list_projects(
        db,
        company_id,
        statuses=[status for status in ProjectStatus if status is not ProjectStatus.REJECTED],
        q=q.strip() if q else None,
        order_by=[Project.updated_at.desc()],
        page=PageParams(limit=limit, offset=0),
    )
    return rows


async def links_of_items(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind, item_ids: Sequence[uuid.UUID]
) -> Sequence[ProjectLink]:
    """Every link of these items, whatever its status: what they are tied to, and what the owner
    said they are not."""
    return await repository.links_of_items(db, company_id, item_kind, item_ids)


async def links_of_project(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID
) -> Sequence[ProjectLink]:
    return await repository.links_of_project(db, company_id, project_id)


async def find_by_number(db: AsyncSession, company_id: uuid.UUID, number: str) -> Project | None:
    """The project that has, or once had, this number: a strong key for linking."""
    found = await repository.find_by_number(db, company_id, numbering.normalise(number))
    return await repository.get_project(db, company_id, found.project_id) if found else None


# Numbering


async def get_numbering(db: AsyncSession, actor: Actor) -> NumberingOut:
    row = await repository.get_numbering(db, actor.company_id)
    number_format = row.format if row else numbering.DEFAULT_FORMAT
    next_seq = row.next_seq if row else 1
    now = clock.now(await _company(db, actor.company_id))
    return NumberingOut(
        format=number_format, next_number=numbering.render(number_format, now, next_seq)
    )


async def set_numbering(
    db: AsyncSession, actor: Actor, *, number_format: str, next_seq: int | None
) -> NumberingOut:
    actor.require(Role.OWNER)
    try:
        numbering.validate(number_format)
    except numbering.InvalidFormat as exc:
        raise BadRequest(str(exc), field="format") from exc
    row = await repository.lock_numbering(db, actor.company_id, numbering.DEFAULT_FORMAT)
    row.format = number_format
    if next_seq is not None:
        row.next_seq = next_seq
    await db.commit()
    return await get_numbering(db, actor)


async def _draw_number(db: AsyncSession, company_id: uuid.UUID) -> str:
    row = await repository.lock_numbering(db, company_id, numbering.DEFAULT_FORMAT)
    now = clock.now(await _company(db, company_id))
    # Skips numbers the owner already typed by hand, so a drawn number is never taken.
    for seq in range(row.next_seq, row.next_seq + 10_000):
        candidate = numbering.render(row.format, now, seq)
        if await repository.find_by_number(db, company_id, numbering.normalise(candidate)) is None:
            row.next_seq = seq + 1
            return candidate
    raise Conflict("No free project number in this format; change the format")


async def _claim_number(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID, number: str
) -> None:
    normalised = numbering.normalise(number)
    existing = await repository.find_by_number(db, company_id, normalised)
    if existing is not None:
        if existing.project_id == project_id:
            return  # going back to one of its own earlier numbers
        raise NumberTaken("Another project has, or once had, this number", field="number")
    try:
        async with db.begin_nested():
            await repository.add(
                db,
                ProjectNumber(
                    company_id=company_id,
                    project_id=project_id,
                    number=number,
                    normalised=normalised,
                ),
            )
    except IntegrityError as exc:  # taken between the check and the insert
        if violated_constraint(exc) != _NUMBER_TAKEN:
            raise
        raise NumberTaken("Another project just took this number", field="number") from exc


# Field provenance


async def apply_fields(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    target: ProposalTarget,
    row: Any,
    values: Mapping[str, Any],
    source: FieldSource,
    evidence: Sequence[uuid.UUID] = (),
) -> tuple[dict[str, Any], list[FieldProposal]]:
    """Decides, field by field, whether ``values`` may be written onto ``row`` (a project, a
    shipment: anything with those attributes and ``field_sources``). Returns the values to write,
    which the caller sets, and the proposals made for those it may not replace. Records the new
    sources on ``row``. Values must be JSON: a date as its ISO text.

    A value equal to the current one is not a change; from the owner it still makes the field the
    owner's."""
    writes: dict[str, Any] = {}
    proposals: list[FieldProposal] = []
    sources = dict(row.field_sources)
    for field, value in values.items():
        current_source = fields.source_of(sources, field)
        if _json(getattr(row, field)) == _json(value):
            if source is FieldSource.OWNER:
                sources[field] = source
            continue
        if fields.may_replace(current_source, source):
            writes[field] = value
            sources[field] = source
        else:
            proposals.append(
                await repository.upsert_open_proposal(
                    db,
                    company_id=company_id,
                    target=target,
                    target_id=row.id,
                    field=field,
                    value=_json(value),
                    source=source,
                    evidence=evidence,
                )
            )
    row.field_sources = sources
    return writes, proposals


async def _write_project_fields(
    db: AsyncSession,
    project: Project,
    values: Mapping[str, Any],
    source: FieldSource,
    evidence: Sequence[uuid.UUID],
) -> list[FieldProposal]:
    writes, proposals = await apply_fields(
        db,
        company_id=project.company_id,
        target=ProposalTarget.PROJECT,
        row=project,
        values=values,
        source=source,
        evidence=evidence,
    )
    if "name" in writes:
        name = str(writes["name"]).strip()
        if not name:
            raise BadRequest("Name the project", field="name")
        project.name = name
    if "number" in writes:
        number = str(writes["number"]).strip()
        await _claim_number(db, project.company_id, project.id, number)
        project.number = number
    days = {
        field: date.fromisoformat(day) if isinstance(day, str) else day
        for field in _DATES
        if field in writes
        for day in [writes[field]]
    }
    first, last = (days.get(field, getattr(project, field)) for field in _DATES)
    if first and last and last < first:
        if source is FieldSource.OWNER:
            raise BadRequest("The end is before the start", field="ends_on")
        # Read from mail, a span that runs backwards is a misreading: the days stay as they were.
        days = {}
    for field, day in days.items():
        setattr(project, field, day)
    await db.flush()
    return proposals


async def propose_project_fields(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    values: Mapping[str, Any],
    source: FieldSource,
    evidence: Sequence[uuid.UUID],
    leave_owner: Collection[str] = (),
) -> list[FieldProposal]:
    """An extraction or a suggestion for a project's fields: written where nothing better holds
    the field, proposed where the owner (or a stronger source) already chose. A field named in
    ``leave_owner`` is not even proposed once the owner set it, which is checked under the
    project's lock. Does not commit."""
    if source is FieldSource.OWNER or not set(values) <= _PROJECT_FIELDS:
        raise ValueError(f"propose only {sorted(_PROJECT_FIELDS)}, never as the owner")
    project = await _project(db, company_id, project_id, for_update=True)
    kept = {
        field: value
        for field, value in values.items()
        if not (
            field in leave_owner
            and fields.source_of(project.field_sources, field) is FieldSource.OWNER
        )
    }
    return await _write_project_fields(db, project, kept, source, evidence)


async def _accept_project_field(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID, field: str, value: Any
) -> None:
    if field not in _PROJECT_FIELDS:
        raise BadRequest(f"{field} is not an editable project field")
    project = await _project(db, company_id, project_id, for_update=True)
    await _write_project_fields(db, project, {field: value}, FieldSource.OWNER, evidence=())


fields.register_target(ProposalTarget.PROJECT, _accept_project_field)


async def list_proposals(
    db: AsyncSession,
    actor: Actor,
    *,
    statuses: Sequence[ProposalStatus],
    target: ProposalTarget | None,
    target_id: uuid.UUID | None,
    page: PageParams,
) -> ProposalPage:
    rows, total = await repository.list_proposals(
        db, actor.company_id, statuses=statuses, target=target, target_id=target_id, page=page
    )
    return ProposalPage(
        items=[ProposalOut.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def decide_proposal(
    db: AsyncSession, actor: Actor, proposal_id: uuid.UUID, *, accept: bool
) -> ProposalOut:
    """Accepting writes the value as the owner's own, since the owner chose it."""
    actor.require(Role.OWNER)
    proposal = await repository.get_proposal(db, actor.company_id, proposal_id)
    if proposal is None:
        raise NotFound("No such proposal")
    if proposal.status is not ProposalStatus.OPEN:
        raise Conflict(f"This proposal was already {proposal.status}")
    if accept:
        writer = fields.writer_for(proposal.target)
        await writer(db, actor.company_id, proposal.target_id, proposal.field, proposal.value)
    proposal.status = ProposalStatus.ACCEPTED if accept else ProposalStatus.DISMISSED
    proposal.decided_by = actor.user_id
    proposal.decided_at = clock.utcnow()
    await db.commit()
    return ProposalOut.model_validate(proposal)


# Parties on a project


async def list_project_parties(
    db: AsyncSession, actor: Actor, project_ids: Sequence[uuid.UUID]
) -> list[ProjectPartyOut]:
    rows = await repository.list_project_parties(db, actor.company_id, project_ids)
    return await _describe_parties(db, actor.company_id, rows)


async def add_project_party(
    db: AsyncSession,
    actor: Actor,
    *,
    project_id: uuid.UUID,
    party_id: uuid.UUID,
    role: ProjectRole,
    layer: int,
    amount: Decimal | None,
    currency: str | None,
) -> ProjectPartyOut:
    actor.require(*_EDITORS)
    await _project(db, actor.company_id, project_id)
    if party_id not in await parties.get_parties(db, actor.company_id, [party_id]):
        raise NotFound("No such party", field="party_id")
    if (amount is None) != (currency is None):
        raise BadRequest("An amount needs its currency, and a currency its amount", field="amount")
    row = ProjectParty(
        company_id=actor.company_id,
        project_id=project_id,
        party_id=party_id,
        role=role,
        layer=layer,
        amount=amount,
        currency=currency,
    )
    try:
        async with db.begin_nested():
            await repository.add(db, row)
    except IntegrityError as exc:
        if violated_constraint(exc) != "project_parties_project_id_party_id_role_key":
            raise
        raise Conflict("This party already has that role on the project") from exc
    await db.commit()
    return (await _describe_parties(db, actor.company_id, [row]))[0]


async def remove_project_party(db: AsyncSession, actor: Actor, project_party_id: uuid.UUID) -> None:
    actor.require(*_EDITORS)
    row = await repository.get_project_party(db, actor.company_id, project_party_id)
    if row is None:
        raise NotFound("No such party on a project")
    await repository.delete(db, row)
    await db.commit()


async def _describe_parties(
    db: AsyncSession, company_id: uuid.UUID, rows: Sequence[ProjectParty]
) -> list[ProjectPartyOut]:
    names = await parties.get_parties(db, company_id, list({row.party_id for row in rows}))
    return [
        ProjectPartyOut(
            id=row.id,
            project_id=row.project_id,
            party_id=row.party_id,
            party_name=names[row.party_id].name,
            role=row.role,
            layer=row.layer,
            amount=row.amount,
            currency=row.currency,
        )
        for row in rows
    ]


# Links


async def link_item(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    item_kind: ItemKind,
    item_id: uuid.UUID,
    project_id: uuid.UUID,
    method: LinkMethod,
    confidence: float,
    evidence: Sequence[uuid.UUID] = (),
    shipment_id: uuid.UUID | None = None,
    proof_of_delivery: bool = False,
    confirmed_by: uuid.UUID | None = None,
    confirm: bool = False,
) -> ProjectLink:
    """Links an item to a project, suggested unless ``confirm``. Linking the same item to the same
    project again keeps one link: a stronger method or a confirmation updates it, and a link the
    owner rejected stays rejected, until the owner's own confirmation takes that back. Does not
    commit."""
    if not 0 <= confidence <= 1:
        raise ValueError("confidence is between 0 and 1")
    company = await _company(db, company_id)
    link = await repository.get_link_for_item(db, company_id, project_id, item_kind, item_id)
    if link is None:
        link = ProjectLink(
            company_id=company_id,
            item_kind=item_kind,
            item_id=item_id,
            project_id=project_id,
            method=method,
            confidence=confidence,
            evidence=list(evidence),
            status=LinkStatus.SUGGESTED,
            shipment_id=shipment_id,
            proof_of_delivery=proof_of_delivery,
        )
        await _flush_link(db, link, add=True)
    elif link.status is LinkStatus.REJECTED and not (confirm and method is LinkMethod.OWNER):
        return link
    else:
        link.confidence = max(link.confidence, confidence)
        link.evidence = sorted(set(link.evidence) | set(evidence))
        if method is LinkMethod.OWNER:
            link.method = method
        if shipment_id is not None:
            link.shipment_id = shipment_id
        link.proof_of_delivery = link.proof_of_delivery or proof_of_delivery
    confirming = confirm and link.status is not LinkStatus.CONFIRMED
    if confirming:
        link.status = LinkStatus.CONFIRMED
        link.decided_by = confirmed_by
        link.decided_at = clock.now(company)
    await _flush_link(db, link)
    if confirming:
        await repository.activate_if_draft(db, company_id, project_id)
    return link


async def create_link(
    db: AsyncSession,
    actor: Actor,
    *,
    item_kind: ItemKind,
    item_id: uuid.UUID,
    project_id: uuid.UUID,
    shipment_id: uuid.UUID | None,
    proof_of_delivery: bool,
) -> LinkOut:
    """The owner attaching an item by hand: a confirmed link, method ``owner``."""
    actor.require(Role.OWNER)
    await _project(db, actor.company_id, project_id)
    if not await _item_exists(db, actor.company_id, item_kind, item_id):
        raise NotFound(f"No such {item_kind}", field="item_id")
    link = await link_item(
        db,
        company_id=actor.company_id,
        item_kind=item_kind,
        item_id=item_id,
        project_id=project_id,
        method=LinkMethod.OWNER,
        confidence=1.0,
        shipment_id=shipment_id,
        proof_of_delivery=proof_of_delivery,
        confirmed_by=actor.user_id,
        confirm=True,
    )
    if item_kind is ItemKind.MESSAGE:
        await carry_files(db, actor.company_id, [item_id])
    await db.commit()
    return LinkOut.model_validate(link)


async def update_link(
    db: AsyncSession, actor: Actor, link_id: uuid.UUID, changes: Mapping[str, Any]
) -> LinkOut:
    """Confirm, reject, or place on a truck. Moving an item to another project is a reject here
    and a new link there."""
    actor.require(Role.OWNER)
    link = await repository.get_link(db, actor.company_id, link_id, for_update=True)
    if link is None:
        raise NotFound("No such link")
    company = await _company(db, actor.company_id)
    if not changes:
        # Nothing was answered: the link stays whoever's it is.
        return LinkOut.model_validate(link)
    # The owner's answer: code never changes this link again (ADR-0021).
    link.method = LinkMethod.OWNER
    if "shipment_id" in changes:
        link.shipment_id = changes["shipment_id"]
    if changes.get("proof_of_delivery") is not None:
        link.proof_of_delivery = bool(changes["proof_of_delivery"])
    status = changes.get("status")
    if status is not None and LinkStatus(status) is not link.status:
        link.status = LinkStatus(status)
        link.decided_by = actor.user_id
        link.decided_at = clock.now(company)
    await _flush_link(db, link)
    if link.status is LinkStatus.CONFIRMED:
        await repository.activate_if_draft(db, actor.company_id, link.project_id)
    if link.item_kind is ItemKind.MESSAGE:
        await carry_files(db, actor.company_id, [link.item_id])
    await db.commit()
    return LinkOut.model_validate(link)


async def decide_links(
    db: AsyncSession,
    actor: Actor,
    link_ids: Sequence[uuid.UUID],
    *,
    confirm: bool,
    withdraw: bool = False,
) -> int:
    """The owner confirming or rejecting several suggested links in one step. A link already
    decided keeps its answer, unless the owner takes a confirmation back (``withdraw``, with a
    rejection): the item is being moved to another project. Returns how many were decided. Does
    not commit."""
    actor.require(Role.OWNER)
    company = await _company(db, actor.company_id)
    decided = []
    open_to = {LinkStatus.SUGGESTED, *((LinkStatus.CONFIRMED,) if withdraw and not confirm else ())}
    for link in await repository.get_links(db, actor.company_id, link_ids, for_update=True):
        if link.status not in open_to:
            continue
        link.status = LinkStatus.CONFIRMED if confirm else LinkStatus.REJECTED
        link.method = LinkMethod.OWNER
        link.decided_by = actor.user_id
        link.decided_at = clock.now(company)
        decided.append(link)
    if decided:
        # One write for them all, with the refusals a single link's write maps.
        await _flush_link(db, decided[-1])
    if confirm:
        for project_id in {link.project_id for link in decided}:
            await repository.activate_if_draft(db, actor.company_id, project_id)
    mails = [link.item_id for link in decided if link.item_kind is ItemKind.MESSAGE]
    await carry_files(db, actor.company_id, mails)
    return len(decided)


async def carry_all_files(db: AsyncSession, company_id: uuid.UUID) -> dict[LinkStatus, int]:
    """Brings every linked mail's files into its projects: for links made before files followed
    their mail. Returns the files' mail links by status. Does not commit."""
    mails = await repository.linked_items(db, company_id, ItemKind.MESSAGE)
    for start in range(0, len(mails), _CARRY_BATCH):
        await carry_files(db, company_id, mails[start : start + _CARRY_BATCH])
    return await repository.mail_link_counts(db, company_id)


async def carry_files(
    db: AsyncSession, company_id: uuid.UUID, message_ids: Sequence[uuid.UUID]
) -> None:
    """Brings the links of the files these mails carried up to the mails' own links (ADR-0021).
    A file is in each project a mail that carried it is linked to: confirmed if one of those
    mails' links there is confirmed, else suggested, else rejected. Only `mail` links are
    written, so a file the owner or code linked another way keeps its link; and a file leaves a
    project no mail that carried it is linked to any more. Rejected projects take no files. Does
    not commit."""
    carried = await conversations.carried_files(db, company_id, message_ids)
    if not carried:
        return
    await repository.lock_files(db, company_id, list(carried))
    mails = sorted({mail for carriers in carried.values() for mail in carriers})
    by_mail: dict[uuid.UUID, list[ProjectLink]] = {}
    for link in await repository.links_of_items(db, company_id, ItemKind.MESSAGE, mails):
        by_mail.setdefault(link.item_id, []).append(link)
    named = {link.project_id for links in by_mail.values() for link in links}
    open_projects = {
        project_id
        for project_id, project in (await get_projects(db, company_id, sorted(named))).items()
        if project.status is not ProjectStatus.REJECTED
    }
    now = clock.now(await _company(db, company_id))
    rows: list[dict[str, Any]] = []
    keep: list[tuple[uuid.UUID, uuid.UUID]] = []
    for document_id, carriers in carried.items():
        held: dict[uuid.UUID, tuple[LinkStatus, float]] = {}
        for link in (link for mail in carriers for link in by_mail.get(mail, [])):
            if link.project_id not in open_projects:
                continue
            status, confidence = held.get(link.project_id, (LinkStatus.REJECTED, 0.0))
            if _LINK_RANK[link.status] > _LINK_RANK[status]:
                status = link.status
            held[link.project_id] = (status, max(confidence, link.confidence))
        keep.extend((document_id, project_id) for project_id in sorted(held))
        rows.extend(
            {
                "id": uuid.uuid7(),
                "company_id": company_id,
                "item_kind": ItemKind.DOCUMENT,
                "item_id": document_id,
                "project_id": project_id,
                "method": LinkMethod.MAIL,
                "confidence": confidence,
                "evidence": [],
                "status": status,
                "proof_of_delivery": False,
                "decided_at": None if status is LinkStatus.SUGGESTED else now,
            }
            for project_id, (status, confidence) in sorted(held.items())
        )
    await repository.drop_mail_links(db, company_id, sorted(carried), keep=keep)
    await repository.upsert_mail_links(db, rows)


async def _carry_unpacked(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """An archive's files were taken out after its mail was linked: they join the mail's
    projects, as the archive did."""
    archives = [document_id, *await documents.containers_of(db, company_id, document_id)]
    carriers = await conversations.messages_attaching(db, company_id, archives)
    await carry_files(
        db, company_id, sorted({mail for mails in carriers.values() for mail in mails})
    )


documents.on_unpacked(_carry_unpacked)


async def list_links(
    db: AsyncSession,
    actor: Actor,
    *,
    project_id: uuid.UUID | None,
    shipment_id: uuid.UUID | None,
    statuses: Sequence[LinkStatus],
    item_kind: ItemKind | None,
    item_id: uuid.UUID | None,
    with_items: bool = False,
    page: PageParams,
) -> LinkPage:
    rows, total = await repository.list_links(
        db,
        actor.company_id,
        project_id=project_id,
        shipment_id=shipment_id,
        statuses=statuses,
        item_kind=item_kind,
        item_id=item_id,
        newest_first=True,
        page=page,
    )
    links = [LinkOut.model_validate(row) for row in rows]
    if with_items:
        links = await _with_items(db, actor.company_id, links)
    return LinkPage(items=links, total=total, limit=page.limit, offset=page.offset)


async def _with_items(
    db: AsyncSession, company_id: uuid.UUID, links: list[LinkOut]
) -> list[LinkOut]:
    """Each link with what its item is, each kind read in one go."""

    def ids(kind: ItemKind) -> list[uuid.UUID]:
        return list({link.item_id for link in links if link.item_kind is kind})

    file_ids = ids(ItemKind.DOCUMENT)
    messages = await conversations.message_summaries(db, company_id, ids(ItemKind.MESSAGE))
    files = await documents.document_outs(db, company_id, file_ids)
    carriers = await conversations.first_carriers(db, company_id, file_ids)
    invoices = {
        each.id: each for each in await books.get_invoices(db, company_id, ids(ItemKind.INVOICE))
    }
    briefs: dict[uuid.UUID, ItemBrief] = {}
    for id_, message in messages.items():
        briefs[id_] = MessageBrief(
            item_kind="message", at=message.sent_at, channel=message.channel, message=message
        )
    for id_, file in files.items():
        carrier = carriers.get(id_)
        briefs[id_] = DocumentBrief(
            item_kind="document",
            at=carrier.sent_at if carrier else file.created_at,
            channel=carrier.channel if carrier else Channel.UPLOAD,
            document=file,
            message_id=carrier.message_id if carrier else None,
        )
    for id_, invoice in invoices.items():
        briefs[id_] = InvoiceBrief(
            item_kind="invoice",
            at=(
                datetime.combine(invoice.issue_date, _NOON, tzinfo=UTC)
                if invoice.issue_date
                else invoice.synced_at
            ),
            channel="books",
            invoice=invoice,
        )
    return [link.model_copy(update={"item": briefs.get(link.item_id)}) for link in links]


async def _flush_link(db: AsyncSession, link: ProjectLink, *, add: bool = False) -> None:
    try:
        async with db.begin_nested():
            if add:
                db.add(link)
            await db.flush()
    except IntegrityError as exc:
        constraint = violated_constraint(exc)
        if constraint == _ONE_PROOF:
            raise Conflict(
                "This proof of delivery already confirms another truck; reject that link first"
            ) from exc
        if constraint == _SHIPMENT_KEY:
            raise BadRequest("That truck is not on this project", field="shipment_id") from exc
        raise


async def _links_document(db: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    return await repository.links_item(db, company_id, ItemKind.DOCUMENT, document_id)


# A project's evidence stays with the project: a linked file, even a rejected link's, is kept, so
# no link ever names a file that is gone.
documents.register_holder(Holder.PROJECT, _links_document)


async def _item_exists(
    db: AsyncSession, company_id: uuid.UUID, item_kind: ItemKind, item_id: uuid.UUID
) -> bool:
    match item_kind:
        case ItemKind.MESSAGE:
            return await conversations.message_exists(db, company_id, item_id)
        case ItemKind.DOCUMENT:
            return await documents.document_exists(db, company_id, item_id)
        case ItemKind.INVOICE:
            return await books.invoice_exists(db, company_id, item_id)
        case ItemKind.LOCATION:
            return False  # a position belongs to its truck; it is never attached by hand


# Shared


async def _project(
    db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID, *, for_update: bool = False
) -> Project:
    project = await repository.get_project(db, company_id, project_id, for_update=for_update)
    if project is None:
        raise NotFound("No such project")
    return project


async def _company(db: AsyncSession, company_id: uuid.UUID) -> clock.CompanyClock:
    return (await companies.get_companies(db, [company_id]))[company_id]


async def _describe(
    db: AsyncSession, company_id: uuid.UUID, projects: Sequence[Project]
) -> list[ProjectOut]:
    numbers = await repository.numbers_of(db, company_id, [p.id for p in projects])
    links = await repository.count_links(db, company_id, [p.id for p in projects])
    jobs: dict[uuid.UUID, ProjectJob] = {}
    for reader in _job_readers:
        jobs |= await reader(db, company_id, [p.id for p in projects])
    aliases: dict[uuid.UUID, list[str]] = {}
    for number in numbers:
        aliases.setdefault(number.project_id, []).append(number.number)
    return [
        ProjectOut(
            id=project.id,
            number=project.number,
            aliases=[n for n in aliases.get(project.id, []) if n != project.number],
            name=project.name,
            starts_on=project.starts_on,
            ends_on=project.ends_on,
            status=project.status,
            stage=project.stage,
            field_sources={k: FieldSource(v) for k, v in project.field_sources.items()},
            confirmed_links=links.get((project.id, LinkStatus.CONFIRMED), 0),
            suggested_links=links.get((project.id, LinkStatus.SUGGESTED), 0),
            job=jobs.get(project.id),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        for project in projects
    ]


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):  # a date
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
