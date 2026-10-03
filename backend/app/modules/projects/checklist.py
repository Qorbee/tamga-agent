"""What a job still lacks: each project's and each truck's checklist (docs/architecture/09).

A pack names the items and reads where each stands from the project's links, its trucks and its
books; this module keeps the owner's marks over them (not needed, needed, done) and serves the
result. The Inbox's missing papers and the payment rules read the same items, so an item is done
by a paper only when its link is confirmed, and a proof of delivery only as ADR-0021 counts it:
the owner's mark never makes one.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.projects import repository
from app.modules.projects.models import ChecklistMark, ChecklistMarkKind
from app.modules.projects.schemas import (
    ChecklistItemOut,
    ChecklistMarkOut,
    ChecklistMarkSet,
    ChecklistState,
)
from app.platform.auth import Actor, Role
from app.platform.db import violated_constraint
from app.platform.errors import BadRequest, NotFound

_TRUCK_KEY = "checklist_marks_company_id_project_id_shipment_id_fkey"


@dataclass(frozen=True, slots=True)
class Item:
    """One item of the template: a truck's when ``per_truck``, else the project's."""

    key: str
    per_truck: bool
    # Asked of every job unless the owner marks it not needed.
    required: bool = True
    # Whether the owner's "done" fills it without a paper.
    done_by_mark: bool = True


@dataclass(frozen=True, slots=True)
class Found:
    """Where one item stands by what Tamga holds: ``done`` by a confirmed paper, ``waiting`` on
    the owner or on a payment, ``missing``."""

    project_id: uuid.UUID
    shipment_id: uuid.UUID | None
    key: str
    state: Literal["done", "waiting", "missing"]
    link_ids: tuple[uuid.UUID, ...] = ()


# Reads every item of these projects and of their trucks not rejected: (db, company, projects).
type Reader = Callable[[AsyncSession, uuid.UUID, Sequence[uuid.UUID]], Awaitable[Sequence[Found]]]

_items: dict[str, Item] = {}
_readers: list[Reader] = []


def register(items: Sequence[Item], reader: Reader) -> None:
    for item in items:
        if item.key in _items:
            raise ValueError(f"checklist item {item.key} is registered twice")
        _items[item.key] = item
    _readers.append(reader)


async def list_items(
    db: AsyncSession,
    actor: Actor,
    project_ids: Sequence[uuid.UUID],
    *,
    shipment_id: uuid.UUID | None = None,
    states: Sequence[ChecklistState] = (),
) -> list[ChecklistItemOut]:
    """The checklists of these projects and their trucks: each project's own items, then each
    truck's, in the template's order. One truck's with ``shipment_id``; only the items in
    ``states`` when given."""
    found_ids = {p.id for p in await repository.get_projects(db, actor.company_id, project_ids)}
    rank = {project_id: index for index, project_id in enumerate(project_ids)}
    ids = sorted(found_ids, key=rank.__getitem__)
    if not ids:
        return []
    marks = {
        (mark.project_id, mark.shipment_id, mark.key): mark
        for mark in await repository.marks_of_projects(db, actor.company_id, ids)
    }
    found = [entry for reader in _readers for entry in await reader(db, actor.company_id, ids)]
    groups: dict[tuple[uuid.UUID, uuid.UUID | None], int] = {}
    for entry in found:
        groups.setdefault((entry.project_id, entry.shipment_id), len(groups))
    order = {key: index for index, key in enumerate(_items)}
    found.sort(
        key=lambda entry: (
            rank[entry.project_id],
            entry.shipment_id is not None,
            groups[(entry.project_id, entry.shipment_id)],
            order[entry.key],
        )
    )
    out = [
        _out(entry, _items[entry.key], marks.get((entry.project_id, entry.shipment_id, entry.key)))
        for entry in found
        if shipment_id is None or entry.shipment_id == shipment_id
    ]
    return [item for item in out if not states or item.state in states]


def _out(found: Found, item: Item, mark: ChecklistMark | None) -> ChecklistItemOut:
    required = item.required
    if mark is not None and mark.mark is not ChecklistMarkKind.DONE:
        required = mark.mark is ChecklistMarkKind.NEEDED
    state: ChecklistState = found.state
    if found.state != "done":
        if mark is not None and mark.mark is ChecklistMarkKind.DONE and item.done_by_mark:
            state = "done"
        elif not required:
            state = "not_needed"
    return ChecklistItemOut(
        project_id=found.project_id,
        shipment_id=found.shipment_id,
        key=found.key,
        state=state,
        required=required,
        link_ids=list(found.link_ids),
        mark=ChecklistMarkOut.model_validate(mark) if mark is not None else None,
    )


async def set_mark(db: AsyncSession, actor: Actor, body: ChecklistMarkSet) -> ChecklistMarkOut:
    """The owner's word on one item; a second mark on the item replaces the first."""
    actor.require(Role.OWNER)
    item = _items.get(body.key)
    if item is None:
        raise BadRequest("No such checklist item", field="key")
    if item.per_truck != (body.shipment_id is not None):
        raise BadRequest(
            "Name the truck" if item.per_truck else "This item is the project's, not a truck's",
            field="shipment_id",
        )
    if body.mark is ChecklistMarkKind.DONE and not item.done_by_mark:
        raise BadRequest("Mark the paper that proves it instead", field="mark")
    if not await repository.get_projects(db, actor.company_id, [body.project_id]):
        raise NotFound("No such project", field="project_id")
    try:
        async with db.begin_nested():
            mark = await repository.set_mark(
                db, actor.company_id, body.project_id, body.shipment_id, body.key, body.mark
            )
    except IntegrityError as exc:
        if violated_constraint(exc) == _TRUCK_KEY:
            raise BadRequest("That truck is not on this project", field="shipment_id") from exc
        raise
    out = ChecklistMarkOut.model_validate(mark)
    await db.commit()
    return out


async def delete_mark(db: AsyncSession, actor: Actor, mark_id: uuid.UUID) -> None:
    """Takes the owner's mark off: the item stands as its template and its papers say."""
    actor.require(Role.OWNER)
    mark = await repository.get_mark(db, actor.company_id, mark_id)
    if mark is None:
        raise NotFound("No such mark")
    await db.delete(mark)
    await db.commit()


async def marked_trucks(
    db: AsyncSession, company_id: uuid.UUID, shipment_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """The trucks the owner marked an item of."""
    if not shipment_ids:
        return set()
    return await repository.marked_trucks(db, company_id, shipment_ids)
