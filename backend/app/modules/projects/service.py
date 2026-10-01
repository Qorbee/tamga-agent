"""Projects: the files a broker's work is rebuilt into, and the evidence tying items to them
(docs/architecture/09, ADR-0010).

- A project's number is drawn in the company's own format, and every number it ever had stays its
  alias: a number once used is never given to another project.
- Every editable field records who set it. The owner's value is never overwritten by an
  extraction or a suggestion; that value waits as a field proposal until the owner decides.
- A link carries its method, confidence, the facts it cites and who decided it. Only a confirmed
  link is proof for money, and a proof of delivery confirms at most one truck.

Operations called by other modules and packs (``link_item``, ``apply_fields``) run in the caller's
transaction and never commit; those behind the API commit.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import ColumnElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.conversations import service as conversations
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
    LinkOut,
    LinkPage,
    NumberingOut,
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
    "created_at": Project.created_at.asc(),
    "-created_at": Project.created_at.desc(),
    "updated_at": Project.updated_at.asc(),
    "-updated_at": Project.updated_at.desc(),
}
# Editable project fields; stage is computed, status moves only through _TRANSITIONS.
_PROJECT_FIELDS: Final = frozenset({"name", "number"})


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
        field_sources={"name": source, "number": source if number else FieldSource.SUGGESTION},
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
    sort: ProjectSort,
    page: PageParams,
) -> ProjectPage:
    rows, total = await repository.list_projects(
        db,
        actor.company_id,
        statuses=statuses,
        q=q.strip() if q else None,
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
    status: ProjectStatus | None,
) -> ProjectOut:
    actor.require(*_EDITORS)
    project = await _project(db, actor.company_id, project_id, for_update=True)
    if status is not None and status is not project.status:
        actor.require(Role.OWNER)
        if status not in _TRANSITIONS.get(project.status, frozenset()):
            raise BadRequest(f"A {project.status} project cannot become {status}", field="status")
        project.status = status
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if number is not None:
        values["number"] = number
    await _write_project_fields(db, project, values, FieldSource.OWNER, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [project]))[0]


async def project_exists(db: AsyncSession, company_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    return await repository.get_project(db, company_id, project_id) is not None


async def get_projects(
    db: AsyncSession, company_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Project]:
    return {p.id: p for p in await repository.get_projects(db, company_id, ids)}


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
) -> list[FieldProposal]:
    """An extraction or a suggestion for a project's fields: written where nothing better holds
    the field, proposed where the owner (or a stronger source) already chose. Does not commit."""
    if source is FieldSource.OWNER or not set(values) <= _PROJECT_FIELDS:
        raise ValueError(f"propose only {sorted(_PROJECT_FIELDS)}, never as the owner")
    project = await _project(db, company_id, project_id, for_update=True)
    return await _write_project_fields(db, project, values, source, evidence)


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
    owner rejected stays rejected. Does not commit."""
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
    elif link.status is LinkStatus.REJECTED:
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
    await db.commit()
    return LinkOut.model_validate(link)


async def list_links(
    db: AsyncSession,
    actor: Actor,
    *,
    project_id: uuid.UUID | None,
    shipment_id: uuid.UUID | None,
    statuses: Sequence[LinkStatus],
    item_kind: ItemKind | None,
    item_id: uuid.UUID | None,
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
    return LinkPage(
        items=[LinkOut.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


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
    aliases: dict[uuid.UUID, list[str]] = {}
    for number in numbers:
        aliases.setdefault(number.project_id, []).append(number.number)
    return [
        ProjectOut(
            id=project.id,
            number=project.number,
            aliases=[n for n in aliases.get(project.id, []) if n != project.number],
            name=project.name,
            status=project.status,
            stage=project.stage,
            field_sources={k: FieldSource(v) for k, v in project.field_sources.items()},
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
