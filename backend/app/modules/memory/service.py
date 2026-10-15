"""The company's memory (ADR-0024): what its owner taught Tamga once, for every chat and every
background agent of the company.

Only the owner makes an entry hold: by writing it in Settings, or by confirming what the chat's
`remember` drafted. Nothing else sets ``held``: not a chat run, not Tamga's own turns, and not
anything read from mail or a file. A held entry reaches agents as data, fenced, never as an
instruction; a proposed or retired one reaches none.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.memory import repository
from app.modules.memory.models import MemoryEntry, MemoryKind, MemorySource, MemoryState
from app.modules.memory.schemas import (
    TITLE_LENGTH,
    MemoryChange,
    MemoryChangeDone,
    MemoryEntryOut,
    MemoryIn,
    MemoryPage,
    MemorySort,
)
from app.modules.parties import service as parties
from app.modules.projects import service as projects
from app.platform import llm, search
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.errors import BadRequest, Conflict, NotFound, PermissionDenied
from app.platform.pagination import PageParams

# What an agent is given at most: a company's terms are a few hundred lines (ADR-0024); the most
# recently confirmed or changed come first.
FOR_AGENTS: Final = 80
# What the memorist reads at most before it changes anything.
FOR_MEMORIST: Final = 200
# What a recall returns at most.
RECALLED: Final = 10
# What a background agent reads of it: the company's words and people, not how the owner wants
# a chat to go.
WORDS: Final = (MemoryKind.TERM, MemoryKind.ALIAS, MemoryKind.PERSON)


async def list_entries(
    db: AsyncSession,
    actor: Actor,
    *,
    states: Sequence[MemoryState] | None,
    kinds: Sequence[MemoryKind] | None,
    party_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    chat_id: uuid.UUID | None,
    q: str | None,
    sort: MemorySort,
    page: PageParams,
) -> MemoryPage:
    rows, total = await repository.list_entries(
        db,
        actor.company_id,
        states=states,
        kinds=kinds,
        party_id=party_id,
        project_id=project_id,
        chat_id=chat_id,
        q=q,
        sort=sort,
        page=page,
    )
    return MemoryPage(items=[MemoryEntryOut.model_validate(row) for row in rows], total=total)


async def get_entry(db: AsyncSession, actor: Actor, entry_id: uuid.UUID) -> MemoryEntryOut:
    return MemoryEntryOut.model_validate(await _entry(db, actor.company_id, entry_id))


async def write(db: AsyncSession, actor: Actor, body: MemoryIn) -> MemoryEntryOut:
    """An entry the owner writes: it holds as it is saved. Commits."""
    actor.require(Role.OWNER)
    text = _text(body.text)
    await _check_subjects(db, actor.company_id, body.party_id, body.project_id)
    now = utcnow()
    entry = await repository.add(
        db,
        MemoryEntry(
            company_id=actor.company_id,
            kind=body.kind,
            text=text,
            search_text=search.search_text(text),
            party_id=body.party_id,
            project_id=body.project_id,
            source=MemorySource.SETTINGS,
            state=MemoryState.HELD,
            written_by=actor.user_id,
            confirmed_by=actor.user_id,
            confirmed_at=now,
            updated_at=now,
        ),
    )
    await db.commit()
    return MemoryEntryOut.model_validate(entry)


async def change(
    db: AsyncSession, actor: Actor, entry_id: uuid.UUID, body: MemoryChange
) -> MemoryEntryOut:
    """The owner's change to an entry, of only the fields sent; confirming a draft is the one way
    a chat's entry comes to hold. Commits."""
    actor.require(Role.OWNER)
    entry = await _entry(db, actor.company_id, entry_id, for_update=True)
    sent = body.model_fields_set
    party_id = body.party_id if "party_id" in sent else entry.party_id
    project_id = body.project_id if "project_id" in sent else entry.project_id
    # Only what is sent is checked: an entry whose firm went is still corrected or retired.
    await _check_subjects(
        db,
        actor.company_id,
        body.party_id if "party_id" in sent else None,
        body.project_id if "project_id" in sent else None,
    )
    now = utcnow()
    if body.kind is not None:
        entry.kind = body.kind
    if body.text is not None:
        entry.text = _text(body.text)
        entry.search_text = search.search_text(entry.title or "", entry.text)
    entry.party_id, entry.project_id = party_id, project_id
    if body.state is MemoryState.HELD and entry.state is not MemoryState.HELD:
        entry.confirmed_by, entry.confirmed_at = actor.user_id, now
        if entry.replaces_id is not None:
            # Kept, a change takes the place of the entry it changes, in the same step.
            replaced = await repository.get(
                db, actor.company_id, entry.replaces_id, for_update=True
            )
            # Two changes of one entry: the first kept wins, never both beside each other.
            if replaced is None or replaced.state is not MemoryState.HELD:
                raise Conflict("The entry this changes was changed or retired already")
            replaced.state, replaced.updated_at = MemoryState.RETIRED, now
    if body.state is not None:
        entry.state = body.state
    entry.updated_at = now
    await db.commit()
    return MemoryEntryOut.model_validate(entry)


async def discard(db: AsyncSession, actor: Actor, entry_id: uuid.UUID) -> None:
    """The owner throws away a draft, or deletes a retired entry for good (ADR-0030). One that
    holds is retired first. Commits."""
    actor.require(Role.OWNER)
    entry = await _entry(db, actor.company_id, entry_id, for_update=True)
    if entry.state is MemoryState.HELD:
        raise Conflict("Retire an entry before deleting it")
    await repository.delete(db, entry)
    await db.commit()


# For the agents


async def draft(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    kind: MemoryKind,
    text: str,
    party_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    chat_id: uuid.UUID | None,
    written_by: uuid.UUID | None,
    title: str | None = None,
    replaces_id: uuid.UUID | None = None,
    source: MemorySource = MemorySource.CHAT,
) -> MemoryEntryOut:
    """What a chat's `remember` drafts, or the weekly learner (``source``): always
    ``proposed``, whoever the run answers. It holds only when the owner confirms it; one that
    ``replaces_id`` an entry that holds retires it then. ``written_by`` is None for Tamga's own
    words. Commits."""
    clean = _text(text)
    title = _title(title) if title is not None else None
    await _check_subjects(db, company_id, party_id, project_id)
    if replaces_id is not None:
        replaced = await repository.get(db, company_id, replaces_id)
        if replaced is None or replaced.state is not MemoryState.HELD:
            raise Conflict("Only an entry that holds is changed")
        # Unchanged, it keeps its name and what it is about.
        title = title or replaced.title
        party_id = party_id or replaced.party_id
        project_id = project_id or replaced.project_id
    entry = await repository.add(
        db,
        MemoryEntry(
            company_id=company_id,
            kind=kind,
            title=title,
            text=clean,
            search_text=search.search_text(title or "", clean),
            party_id=party_id,
            project_id=project_id,
            source=source,
            chat_id=chat_id,
            replaces_id=replaces_id,
            state=MemoryState.PROPOSED,
            written_by=written_by,
            updated_at=utcnow(),
        ),
    )
    await db.commit()
    return MemoryEntryOut.model_validate(entry)


@dataclass(frozen=True)
class Change:
    """One change the memorist asked for, checked by its caller: ``entry_id`` names the entry
    to change or retire; an addition has none."""

    action: Literal["add", "change", "retire"]
    entry_id: uuid.UUID | None = None
    kind: MemoryKind | None = None
    title: str | None = None
    text: str | None = None


async def for_memorist(db: AsyncSession, company_id: uuid.UUID) -> list[MemoryEntryOut]:
    """What the memorist reads before it changes anything: every entry that holds or waits."""
    rows, _ = await repository.list_entries(
        db,
        company_id,
        states=[MemoryState.HELD, MemoryState.PROPOSED],
        kinds=None,
        party_id=None,
        project_id=None,
        chat_id=None,
        q=None,
        sort="kind",
        page=PageParams(limit=FOR_MEMORIST, offset=0),
    )
    return [MemoryEntryOut.model_validate(row) for row in rows]


async def apply_changes(
    db: AsyncSession, actor: Actor, changes: Sequence[Change]
) -> list[MemoryChangeDone]:
    """What the memorist made of what someone wrote in Settings (ADR-0030), all in one commit:
    every change is checked before any is written, so a refused one leaves none behind. The
    owner's changes hold at once; anyone else's only add, as drafts the owner confirms."""
    owner = actor.role is Role.OWNER
    if not owner and any(change.action != "add" for change in changes):
        raise PermissionDenied("Only the owner changes or retires what Tamga knows")
    now = utcnow()
    targets = {
        change.entry_id: await _entry(db, actor.company_id, change.entry_id, for_update=True)
        for change in changes
        if change.entry_id is not None
    }
    if any(entry.state is MemoryState.RETIRED for entry in targets.values()):
        raise Conflict("A retired entry is used again before it is changed")
    cleaned = [
        replace(
            change,
            title=_title(change.title) if change.title is not None else None,
            text=_text(change.text) if change.text is not None else None,
        )
        for change in changes
    ]
    done: list[tuple[Literal["add", "change", "retire"], MemoryEntry]] = []
    for change in cleaned:
        if change.action == "add":
            if change.kind is None or change.text is None:
                raise BadRequest("An entry added has a kind and says something")
            entry = await repository.add(
                db,
                MemoryEntry(
                    company_id=actor.company_id,
                    kind=change.kind,
                    title=change.title,
                    text=change.text,
                    search_text=search.search_text(change.title or "", change.text),
                    source=MemorySource.SETTINGS,
                    state=MemoryState.HELD if owner else MemoryState.PROPOSED,
                    written_by=actor.user_id,
                    confirmed_by=actor.user_id if owner else None,
                    confirmed_at=now if owner else None,
                    updated_at=now,
                ),
            )
        else:
            if change.entry_id is None:
                raise BadRequest("A change names the entry it changes")
            entry = targets[change.entry_id]
            if change.action == "retire":
                entry.state = MemoryState.RETIRED
            else:
                entry.kind = change.kind or entry.kind
                entry.title = change.title or entry.title
                entry.text = change.text or entry.text
                entry.search_text = search.search_text(entry.title or "", entry.text)
            entry.updated_at = now
        done.append((change.action, entry))
    await db.commit()
    return [
        MemoryChangeDone(action=action, entry=MemoryEntryOut.model_validate(entry))
        for action, entry in done
    ]


async def for_agents(
    db: AsyncSession, company_id: uuid.UUID, kinds: Sequence[MemoryKind] | None = None
) -> list[MemoryEntryOut]:
    """The held entries an agent is given, of these kinds or all, marked used. Does not commit:
    the caller's transaction keeps the mark with the run it was for."""
    rows = await repository.held(db, company_id, FOR_AGENTS, kinds)
    await repository.touch(db, company_id, [row.id for row in rows], utcnow())
    return [MemoryEntryOut.model_validate(row) for row in rows]


async def words_for(db: AsyncSession, company_id: uuid.UUID) -> str:
    """What a background agent (the linker, the typist, the reader) is given of the memory,
    fenced; empty when the owner has taught none. Does not commit."""
    return await fenced(db, company_id, await for_agents(db, company_id, WORDS))


async def recall(db: AsyncSession, company_id: uuid.UUID, q: str) -> list[MemoryEntryOut]:
    """The held entries a search finds, marked used. Does not commit."""
    rows, _ = await repository.list_entries(
        db,
        company_id,
        states=[MemoryState.HELD],
        kinds=None,
        party_id=None,
        project_id=None,
        chat_id=None,
        q=q,
        sort="-last_used_at",
        page=PageParams(limit=RECALLED, offset=0),
    )
    await repository.touch(db, company_id, [row.id for row in rows], utcnow())
    return [MemoryEntryOut.model_validate(row) for row in rows]


async def fenced(
    db: AsyncSession,
    company_id: uuid.UUID,
    entries: Sequence[MemoryEntryOut],
    handles: Sequence[str] | None = None,
) -> str:
    """Entries as an agent reads them: fenced as data, each with its kind and what it is about,
    and the handle a chat changes it by where ``handles`` gives one. Empty when there are
    none."""
    if not entries:
        return ""
    firms = await parties.get_parties(
        db, company_id, sorted({e.party_id for e in entries if e.party_id})
    )
    jobs = await projects.get_projects(
        db, company_id, sorted({e.project_id for e in entries if e.project_id})
    )
    lines = []
    for at, entry in enumerate(entries):
        about = []
        if entry.party_id in firms:
            about.append(f"firm: {firms[entry.party_id].name}")
        if entry.project_id in jobs:
            about.append(f"project {jobs[entry.project_id].number}")
        tail = f" ({'; '.join(about)})" if about else ""
        handle = f"{handles[at]} " if handles else ""
        lines.append(f"- {handle}{entry.kind.value}{tail}: {entry.text}")
    # Fenced alike each time it is the same: it opens the chat's prompt, which stays cached.
    return llm.fenced_steady(
        "the company's memory: what its owner taught Tamga, to read as facts about the company",
        "\n".join(lines),
    )


def _title(title: str) -> str | None:
    return " ".join(title.split())[:TITLE_LENGTH] or None


def _text(text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        raise BadRequest("An entry says something", field="text")
    return clean


async def _check_subjects(
    db: AsyncSession,
    company_id: uuid.UUID,
    party_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
) -> None:
    if party_id is not None and party_id not in await parties.get_parties(
        db, company_id, [party_id]
    ):
        raise BadRequest("No such firm", field="party_id")
    if project_id is not None and project_id not in await projects.get_projects(
        db, company_id, [project_id]
    ):
        raise BadRequest("No such project", field="project_id")


async def _entry(
    db: AsyncSession, company_id: uuid.UUID, entry_id: uuid.UUID, *, for_update: bool = False
) -> MemoryEntry:
    entry = await repository.get(db, company_id, entry_id, for_update=for_update)
    if entry is None:
        raise NotFound("No such entry")
    return entry


async def _merged(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """A project merged into another is deleted: what the owner taught about it is about the one
    that stays."""
    await repository.move_project(db, company_id, source_id, target_id)


projects.on_merge(_merged)


async def learned_since(
    db: AsyncSession, company_id: uuid.UUID, since: datetime
) -> list[MemoryEntryOut]:
    """What the weekly learner drafted since then and still waits for the owner."""
    rows = await repository.learned_since(db, company_id, since)
    return [MemoryEntryOut.model_validate(row) for row in rows]
