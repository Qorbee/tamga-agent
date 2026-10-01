"""Linking: which project each conversation belongs to (docs/architecture/09, ADR-0010).

A conversation sorted as work or automated is placed once it is sorted and again when mail joins
it. Code places what it can, at no cost: new mail in a conversation already placed follows it,
and a conversation that shares a number (an order, a CMR, an invoice) with one project goes to
that project. The ``linker`` agent reads the rest of the work conversations, one at a time, with
tools that search the company's own mail, projects and invoices, within the company's daily
budget of model reads. An automated conversation is never read by a model.

Whatever places it, the result is a proposal: ``project_links`` rows that stay suggested, or a
project that stays proposed, until the owner confirms. A link the owner rejected is never made
again.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from pydantic_ai import UsageLimits
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.companies import service as companies
from app.modules.conversations import service as conversations
from app.modules.conversations.models import ThreadCategory
from app.modules.conversations.service import ThreadRead
from app.modules.documents import service as documents
from app.modules.documents.models import ReadState
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.modules.linking import agents, repository
from app.modules.linking.models import Outcome, Placement
from app.modules.linking.schemas import PlacementOut, PlacementPage
from app.modules.projects import service as projects
from app.modules.projects.models import FieldSource, LinkMethod, LinkStatus, ProjectStatus
from app.platform import llm
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.items import ItemKind
from app.platform.language import Language
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

# Conversations one company has read by the model per run: each takes up to a minute, and they
# are read one after another, so that a project proposed for one is there for the next.
PLACE_BATCH: Final = 10
# With no budget left, what code alone can place is still placed, from this many that wait.
CODE_BATCH: Final = 200
PLACE_TRIES: Final = 3
PLACE_RETRY_AFTER: Final = timedelta(hours=1)
# A conversation's change is stamped before its transaction commits, so one can become visible
# after a later one was already noted. Changes are looked through again from this long before
# the newest one noted; one noted twice is not read twice (repository.note_changed).
_OVERLAP: Final = timedelta(minutes=10)
_PAGE: Final = 500
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
# A number more items than this share names no one job: the company's own reference under every
# mail it sends, a tariff code.
KEY_MAX_ITEMS: Final = 25
# Numbers of one conversation looked up; a statement listing a hundred invoices is not searched
# by each.
_KEYS_LOOKED_UP: Final = 20
# New mail follows its conversation's project when it comes this soon after the last one. Later,
# it is read afresh: a client may answer an old thread with a new order.
FOLLOW_WITHIN: Final = timedelta(days=14)
# The answer, the searches before it, and a retry when it fails the output's checks. A run that
# starts within the day's budget finishes, so a day can end this many reads past it.
_LIMITS: Final = UsageLimits(request_limit=12)

# What a placement's ``placed_by`` holds when code placed it, and when the model gave no answer.
BY_KEYS: Final = "keys"
BY_THREAD: Final = "thread"
BY_OWNER: Final = "owner"
UNANSWERED: Final = "unanswered"
_KEY_CONFIDENCE: Final = 0.9
_THREAD_CONFIDENCE: Final = 0.6
_READ: Final = (ThreadCategory.WORK, ThreadCategory.AUTOMATED)
_INVOICE_NUMBER: Final = "invoice_number"


@dataclass(frozen=True, slots=True)
class _Ties:
    """What ties a conversation's mails to projects right now."""

    # Projects its mails are tied to by a link the owner did not reject.
    held: frozenset[uuid.UUID]
    # Projects the owner said it does not belong to: a rejected link, or a rejected project.
    refused: frozenset[uuid.UUID]
    # Its mails that no link the owner did not reject ties to any project.
    untied: frozenset[uuid.UUID]
    # The longest time between an untied mail and the tied mail nearest to it; None when no
    # mail is tied or none is untied.
    gap: timedelta | None


@dataclass(frozen=True, slots=True)
class _Found:
    """What code knows of a conversation before anything decides."""

    ties: _Ties
    # Projects holding an item that shares a number with it, and its facts that carry the number.
    keys: dict[uuid.UUID, frozenset[uuid.UUID]]
    # Project id → "number: name (status)", and → its number, for every project above.
    named: dict[uuid.UUID, str]
    numbers: dict[uuid.UUID, str]
    # How many of its files have been read.
    files_read: int


@dataclass(frozen=True, slots=True)
class _Decision:
    outcome: Outcome
    placed_by: str
    project_id: uuid.UUID | None = None
    # A project to propose, by its name, when none exists.
    new_name: str | None = None
    alternatives: tuple[uuid.UUID, ...] = ()
    method: LinkMethod = LinkMethod.AGENT
    confidence: float | None = None
    evidence: frozenset[uuid.UUID] = frozenset()
    reason: str = ""
    cited: tuple[uuid.UUID, ...] = ()
    reading: dict[str, Any] | None = None


async def place_pending() -> int:
    """Places the conversations that wait, each company's in the order they became ready. A
    failure outside the conversation (the model's provider, the network) is counted against it,
    and it is tried again next run, up to PLACE_TRIES times, then once an hour. Returns how many
    were placed."""
    async with open_session() as db:
        company_ids = await conversations.companies_with_read_threads(db)
    retry_before = utcnow() - PLACE_RETRY_AFTER
    placed = 0
    for company_id in company_ids:
        await note_changes(company_id)
        async with open_session() as db:
            left = await conversations.model_reads_left(db, company_id)
            waiting = await repository.waiting(
                db,
                company_id,
                tries=PLACE_TRIES,
                retry_before=retry_before,
                limit=PLACE_BATCH if left > 0 else CODE_BATCH,
            )
            company = (await companies.get_companies(db, [company_id])).get(company_id)
        trace_content = company is not None and companies.traces_content(company.kind)
        language = company.language if company is not None else Language.EN
        for thread_id in waiting:
            try:
                done = await place_thread(
                    company_id,
                    thread_id,
                    model_allowed=left > 0,
                    language=language,
                    trace_content=trace_content,
                )
            except Exception as error:  # one conversation that fails never stops the others
                # The error's type only: a provider's error can quote the mail it was sent.
                logger.warning(
                    "placing conversation %s failed: %s", thread_id, type(error).__name__
                )
                async with open_session() as db:
                    await repository.add_failure(db, company_id, thread_id, utcnow())
                    await db.commit()
                continue
            placed += done
            async with open_session() as db:
                left = await conversations.model_reads_left(db, company_id)
    return placed


async def note_changes(company_id: uuid.UUID) -> None:
    """Marks as waiting every conversation that got mail or was sorted since the last look."""
    async with open_session() as db:
        seen = await repository.latest_seen(db, company_id)
        since = seen - _OVERLAP if seen is not None else _EPOCH
        after: tuple[datetime, uuid.UUID] | None = None
        while True:
            page = await conversations.changed_threads(
                db, company_id, since=since, after=after, limit=_PAGE
            )
            for thread_id, changed_at in page:
                await repository.note_changed(db, company_id, thread_id, changed_at)
            if len(page) < _PAGE:
                break
            after = (page[-1][1], page[-1][0])
        await db.commit()


async def place_thread(
    company_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    model_allowed: bool,
    language: Language,
    trace_content: bool,
) -> bool:
    """Places one conversation: by code when a rule decides, else by the ``linker`` agent when
    ``model_allowed``. Returns whether it was placed; one that needs the model and may not have
    it keeps waiting. No transaction is open while the model reads."""
    async with open_session() as db:
        placement = await repository.get(db, company_id, thread_id)
        if placement is None:
            return False
        seen_at = placement.placed_at
        thread = await conversations.read_thread(db, company_id, thread_id)
        if thread is None or thread.category not in _READ or not thread.mails:
            # Sent back to be sorted, or sorted as noise since: nothing reads it until it changes.
            placement.waiting = False
            await db.commit()
            return False
        found = await _look(db, company_id, thread)
        if (
            placement.outcome is not None
            and placement.mails == len(thread.mails)
            and placement.last_mail_at == thread.mails[-1].sent_at
            and placement.files_read == found.files_read
        ):
            # The change brought nothing to read: a copy of a mail, stored from a second mailbox.
            placement.waiting = placement.thread_changed_at > thread.changed_at
            await db.commit()
            return False
        decision = _by_code(thread, placement, found)
        if decision is None:
            if not model_allowed:
                return False
            ties = found.ties
            prompt = agents.case(
                await agents.conversation_text(db, company_id, thread),
                [found.named[project_id] for project_id in sorted(ties.held)],
                [found.named[project_id] for project_id in found.keys],
                [found.named[project_id] for project_id in sorted(ties.refused)],
            )
            desk = agents.Desk(
                company_id=company_id,
                thread_id=thread_id,
                refused={agents.bare(found.numbers[project_id]) for project_id in ties.refused},
            )
            for project_id in (*ties.held, *found.keys):
                desk.show(found.numbers[project_id], project_id)
            # Counted before it is made: a call that fails once the provider has the mail counts.
            await conversations.count_model_reads(db, company_id, agents.LINKER)
            await db.commit()
    if decision is None:
        usage = RunUsage()
        try:
            run = await llm.run(
                agents.linker,
                prompt,
                deps=desk,
                limits=_LIMITS,
                language=language,
                trace_content=trace_content,
                usage=usage,
            )
            decision, further = _from_model(run.output, desk, thread, found), usage.requests - 1
        except llm.NoAnswer as error:
            decision, further = _Decision(Outcome.UNPLACED, UNANSWERED), error.requests - 1
        except Exception:
            # The request that failed was sent the conversation too, after those that answered.
            await _count(company_id, usage.requests)
            raise
        await _count(company_id, further)
    async with open_session() as db:
        applied = await _apply(db, company_id, thread, found, decision, seen_at=seen_at)
        await db.commit()
    return applied


async def _count(company_id: uuid.UUID, requests: int) -> None:
    """Counts the requests of a run after its first against the budget: each was sent the
    conversation again."""
    if requests <= 0:
        return
    async with open_session() as db:
        await conversations.count_model_reads(db, company_id, agents.LINKER, requests=requests)
        await db.commit()


async def _look(db: AsyncSession, company_id: uuid.UUID, thread: ThreadRead) -> _Found:
    mail_ids = [mail.id for mail in thread.mails]
    file_ids = [file.document_id for mail in thread.mails for file in mail.files]
    ties = await _ties(db, company_id, thread)
    keys = await _key_candidates(db, company_id, mail_ids, file_ids)
    known = await projects.get_projects(db, company_id, [*ties.held, *ties.refused, *keys])
    files = await documents.get_documents(db, company_id, file_ids)
    return _Found(
        ties=ties,
        keys={
            project_id: frozenset(fact_ids)
            for project_id, fact_ids in keys.items()
            if project_id not in ties.refused
            and known[project_id].status is not ProjectStatus.REJECTED
        },
        named={
            project_id: f"{project.number}: {project.name} ({project.status})"
            for project_id, project in known.items()
        },
        numbers={project_id: project.number for project_id, project in known.items()},
        files_read=sum(file.read_state is ReadState.READ for file in files.values()),
    )


async def _ties(db: AsyncSession, company_id: uuid.UUID, thread: ThreadRead) -> _Ties:
    links = await projects.links_of_items(
        db, company_id, ItemKind.MESSAGE, [mail.id for mail in thread.mails]
    )
    known = await projects.get_projects(db, company_id, [link.project_id for link in links])
    tied: dict[uuid.UUID, set[uuid.UUID]] = {}
    refused = set()
    for link in links:
        # The owner rejected the link, or the whole project it was proposed as.
        if (
            link.status is LinkStatus.REJECTED
            or known[link.project_id].status is ProjectStatus.REJECTED
        ):
            refused.add(link.project_id)
        else:
            tied.setdefault(link.project_id, set()).add(link.item_id)
    tied_mails = set().union(*tied.values())
    sent = {mail.id: mail.sent_at for mail in thread.mails}
    untied = sent.keys() - tied_mails
    gap = max(
        (
            min(abs(sent[mail_id] - sent[other]) for other in tied_mails)
            for mail_id in untied
            if tied_mails
        ),
        default=None,
    )
    return _Ties(
        held=frozenset(tied),
        refused=frozenset(refused - tied.keys()),
        untied=frozenset(untied),
        gap=gap,
    )


async def _key_candidates(
    db: AsyncSession,
    company_id: uuid.UUID,
    mail_ids: Sequence[uuid.UUID],
    file_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The projects that hold an item sharing a key number with this conversation's mails or
    files, each with the ids of this conversation's facts that carry the number."""
    own = {(ItemKind.MESSAGE, mail_id) for mail_id in mail_ids}
    own |= {(ItemKind.DOCUMENT, file_id) for file_id in file_ids}
    kinds = extractors.key_kinds()
    carried: dict[tuple[str, str], set[uuid.UUID]] = {}
    # A file's own key facts: the same file in another mail is the same paper of the same job.
    on_file: dict[uuid.UUID, set[uuid.UUID]] = {}
    for item_kind, ids in ((ItemKind.MESSAGE, mail_ids), (ItemKind.DOCUMENT, file_ids)):
        for fact in await facts.facts_of_items(
            db, company_id=company_id, item_kind=item_kind, item_ids=ids
        ):
            if fact.kind in kinds:
                carried.setdefault((fact.kind, fact.normalised), set()).add(fact.id)
                if item_kind is ItemKind.DOCUMENT:
                    on_file.setdefault(fact.item_id, set()).add(fact.id)
    looked_up = dict(list(carried.items())[:_KEYS_LOOKED_UP])
    # Every other item that shares a number, with this conversation's facts that carry it.
    sharing: dict[tuple[ItemKind, uuid.UUID], set[uuid.UUID]] = {}
    same = await facts.items_sharing_each(
        db,
        company_id=company_id,
        pairs=list(looked_up),
        # Enough to tell whether too many others share one, however often this one repeats it.
        per_pair=KEY_MAX_ITEMS + 1 + len(own),
    )
    for pair, fact_ids in looked_up.items():
        others = [fact for fact in same[pair] if (fact.item_kind, fact.item_id) not in own]
        if len(others) > KEY_MAX_ITEMS:
            continue
        for other in others:
            sharing.setdefault((other.item_kind, other.item_id), set()).update(fact_ids)
    # A file is stored once however many mails carry it: one of this conversation's files that
    # holds a key is looked for in the other mails that carried it, and in its own links.
    for file_id, fact_ids in on_file.items():
        sharing.setdefault((ItemKind.DOCUMENT, file_id), set()).update(fact_ids)
    # The books hold an invoice under its number or its e-document id, not as a fact.
    for invoice in await books.invoices_named(
        db,
        company_id,
        numbers=[value for kind, value in looked_up if kind == _INVOICE_NUMBER],
        e_document_ids=[value.lower() for _, value in looked_up],
    ):
        names = {extractors.reference(invoice.number or ""), (invoice.e_document_id or "").lower()}
        for (_, normalised), fact_ids in looked_up.items():
            if normalised in names or normalised.lower() in names:
                sharing.setdefault((ItemKind.INVOICE, invoice.id), set()).update(fact_ids)
    # A file is tied to a project through the mail that carried it. One that more mails carry
    # than a job has is no key either: the terms sent with every offer.
    files = [item_id for kind, item_id in sharing if kind is ItemKind.DOCUMENT]
    for file_id, carriers in (
        await conversations.messages_attaching(db, company_id, files)
    ).items():
        elsewhere = [mail_id for mail_id in carriers if (ItemKind.MESSAGE, mail_id) not in own]
        if len(elsewhere) > KEY_MAX_ITEMS:
            continue
        for message_id in elsewhere:
            sharing.setdefault((ItemKind.MESSAGE, message_id), set()).update(
                sharing[(ItemKind.DOCUMENT, file_id)]
            )
    candidates: dict[uuid.UUID, set[uuid.UUID]] = {}
    for item_kind in (ItemKind.MESSAGE, ItemKind.DOCUMENT, ItemKind.INVOICE):
        ids = [item_id for kind, item_id in sharing if kind is item_kind]
        for link in await projects.links_of_items(db, company_id, item_kind, ids):
            if link.status is not LinkStatus.REJECTED:
                candidates.setdefault(link.project_id, set()).update(
                    sharing[(item_kind, link.item_id)]
                )
    return candidates


def _by_code(thread: ThreadRead, placement: Placement, found: _Found) -> _Decision | None:
    """What code can decide without a model, or None when a model has to read it."""
    ties = found.ties
    only_held = next(iter(ties.held)) if len(ties.held) == 1 else None
    # Nothing of it is unplaced (the owner, or an earlier run, tied every mail), or every mail
    # that is came soon before or after one that is tied.
    if only_held is not None and (ties.gap is None or ties.gap <= FOLLOW_WITHIN):
        return _follows(only_held, found, placement)
    fits = ties.held | found.keys.keys()
    if len(found.keys) == 1 and len(fits) == 1:
        project_id = next(iter(found.keys))
        return _Decision(
            Outcome.LINKED,
            BY_KEYS,
            project_id=project_id,
            method=LinkMethod.KEY,
            confidence=_KEY_CONFIDENCE,
            evidence=found.keys[project_id],
        )
    if thread.category is not ThreadCategory.AUTOMATED:
        return None
    # A system's notice is placed by its numbers alone, never read by a model.
    if len(fits) > 1:
        return _Decision(Outcome.ASKED, BY_KEYS, alternatives=tuple(sorted(fits)))
    return _Decision(Outcome.UNPLACED, BY_KEYS)


def _follows(project_id: uuid.UUID, found: _Found, placement: Placement) -> _Decision:
    """New mail goes where its conversation is. What the earlier reading said of it is kept."""
    proposed = placement.outcome is Outcome.PROPOSED and placement.project_id == project_id
    return _Decision(
        Outcome.PROPOSED if proposed else Outcome.LINKED,
        BY_THREAD,
        project_id=project_id,
        method=LinkMethod.SCORED,
        confidence=_THREAD_CONFIDENCE,
        evidence=found.keys.get(project_id, frozenset()),
        reason=placement.reason if placement.project_id == project_id else "",
        cited=tuple(placement.cited) if placement.project_id == project_id else (),
        reading=placement.reading if proposed else None,
    )


def _from_model(
    placing: agents.Placing, desk: agents.Desk, thread: ThreadRead, found: _Found
) -> _Decision:
    """The model's answer as a decision, checked against what the run was shown: a project it
    names is one a tool returned, never one the owner refused for this conversation."""
    reason = " ".join(placing.reason.replace("\x00", "").split())[:500]
    cited = tuple(
        thread.mails[number - 1].id
        for number in dict.fromkeys(placing.cited_mails)
        if 1 <= number <= len(thread.mails)
    )
    unplaced = _Decision(Outcome.UNPLACED, agents.MODEL_PLACED, reason=reason, cited=cited)
    if placing.decision == "link":
        project_id = desk.projects.get(agents.bare(placing.project or ""))
        if project_id is None or project_id in found.ties.refused:
            return unplaced
        return _Decision(
            Outcome.LINKED,
            agents.MODEL_PLACED,
            project_id=project_id,
            confidence=placing.confidence,
            evidence=found.keys.get(project_id, frozenset()),
            reason=reason,
            cited=cited,
        )
    if placing.decision == "ask":
        shown = [desk.projects.get(agents.bare(number)) for number in placing.alternatives]
        fits = sorted({p for p in shown if p is not None and p not in found.ties.refused})
        if len(fits) < 2:
            return unplaced
        return _Decision(
            Outcome.ASKED,
            agents.MODEL_PLACED,
            alternatives=tuple(fits),
            confidence=placing.confidence,
            reason=reason,
            cited=cited,
        )
    if placing.decision == "new_project" and placing.new_project is not None:
        name = " ".join(placing.new_project.name.replace("\x00", "").split())[:300]
        if not name:
            return unplaced
        reading = placing.new_project.model_dump(exclude={"name"}, exclude_none=True)
        return _Decision(
            Outcome.PROPOSED,
            agents.MODEL_PLACED,
            new_name=name,
            confidence=placing.confidence,
            reason=reason,
            cited=cited,
            reading={key: " ".join(str(value).split())[:300] for key, value in reading.items()},
        )
    return unplaced


async def _apply(
    db: AsyncSession,
    company_id: uuid.UUID,
    thread: ThreadRead,
    found: _Found,
    decision: _Decision,
    *,
    seen_at: datetime | None,
) -> bool:
    """Writes a decision: the proposed project when one is new, a suggested link for each mail
    that nothing tied yet, and the placement. A mail already tied to a project stays where it
    is: an old thread answered with a new order sends only the new mail to the new project.
    ``seen_at`` is when the placement was last placed as the run saw it: placed again since (the
    owner answered while a model read), the decision is stale and nothing is written; it keeps
    waiting and is looked at afresh. Returns whether it wrote. Does not commit."""
    placement = await repository.get(db, company_id, thread.id, for_update=True)
    if placement is None or placement.placed_at != seen_at:
        return False
    # The owner may have tied or refused a mail while a model read: what is written goes by the
    # links as they are now, not as they were when the conversation was looked at.
    ties = await _ties(db, company_id, thread)
    if decision.project_id in ties.refused:
        decision = replace(decision, outcome=Outcome.UNPLACED, project_id=None)
    alternatives = [
        project_id for project_id in decision.alternatives if project_id not in ties.refused
    ]
    if decision.outcome is Outcome.ASKED and len(alternatives) < 2:
        decision = replace(decision, outcome=Outcome.UNPLACED)
        alternatives = []
    project_id = decision.project_id
    if decision.new_name:
        project = await projects.new_project(
            db,
            company_id=company_id,
            name=decision.new_name,
            status=ProjectStatus.PROPOSED,
            source=FieldSource.SUGGESTION,
        )
        project_id = project.id
    if project_id is not None and decision.outcome in (Outcome.LINKED, Outcome.PROPOSED):
        for mail_id in sorted(ties.untied):
            await projects.link_item(
                db,
                company_id=company_id,
                item_kind=ItemKind.MESSAGE,
                item_id=mail_id,
                project_id=project_id,
                method=decision.method,
                confidence=decision.confidence or 0.0,
                evidence=sorted(decision.evidence),
            )
    placement.outcome = decision.outcome
    placement.placed_by = decision.placed_by
    placement.project_id = project_id
    placement.alternatives = alternatives
    placement.reason = decision.reason
    placement.confidence = decision.confidence
    placement.cited = list(decision.cited)
    placement.reading = decision.reading
    placement.mails = len(thread.mails)
    placement.last_mail_at = thread.mails[-1].sent_at
    placement.files_read = found.files_read
    placement.placed_at = utcnow()
    placement.failures = 0
    placement.failed_at = None
    # Mail that came while it was read is a newer change than the one read: it waits again.
    placement.waiting = placement.thread_changed_at > thread.changed_at
    return True


# What the owner sees and answers


async def list_placements(
    db: AsyncSession,
    actor: Actor,
    *,
    outcomes: Sequence[Outcome],
    project_ids: Sequence[uuid.UUID],
    page: PageParams,
) -> PlacementPage:
    rows, total = await repository.list_placements(
        db, actor.company_id, outcomes=outcomes, project_ids=project_ids, page=page
    )
    return PlacementPage(
        items=await _describe(db, actor.company_id, rows),
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def decide_placement(
    db: AsyncSession,
    actor: Actor,
    placement_id: uuid.UUID,
    *,
    confirm: bool | None,
    project_id: uuid.UUID | None,
) -> PlacementOut:
    """The owner's answer for a whole conversation. ``confirm``: its links that wait, to the
    project it was placed in, are confirmed or rejected; rejected, it is unplaced until new mail
    has it read again. ``project_id``: the owner names its project: every mail of it that no
    confirmed link holds is tied there, and what was suggested elsewhere is rejected."""
    actor.require(Role.OWNER)
    placement = await repository.get_by_id(db, actor.company_id, placement_id, for_update=True)
    if placement is None:
        raise NotFound("No such placement")
    thread = await conversations.read_thread(db, actor.company_id, placement.thread_id)
    mail_ids = [mail.id for mail in thread.mails] if thread is not None else []
    links = await projects.links_of_items(db, actor.company_id, ItemKind.MESSAGE, mail_ids)
    waiting = [link for link in links if link.status is LinkStatus.SUGGESTED]
    if project_id is not None:
        chosen = (await projects.get_projects(db, actor.company_id, [project_id])).get(project_id)
        if chosen is None or chosen.status is ProjectStatus.REJECTED:
            raise NotFound("No such project", field="project_id")
        confirmed = [link for link in links if link.status is LinkStatus.CONFIRMED]
        held = {link.item_id for link in confirmed}
        free = [mail_id for mail_id in mail_ids if mail_id not in held]
        if not free and all(link.project_id != project_id for link in confirmed):
            # Answered before, in another tab perhaps: every mail is confirmed elsewhere, and
            # saying it is here would leave the placement and its links apart.
            raise Conflict("This conversation is already confirmed in another project")
        await projects.decide_links(
            db,
            actor,
            [link.id for link in waiting if link.project_id != project_id],
            confirm=False,
        )
        for mail_id in free:
            await projects.link_item(
                db,
                company_id=actor.company_id,
                item_kind=ItemKind.MESSAGE,
                item_id=mail_id,
                project_id=project_id,
                method=LinkMethod.OWNER,
                confidence=1.0,
                confirmed_by=actor.user_id,
                confirm=True,
            )
        _answered(placement, Outcome.LINKED, project_id)
    else:
        if placement.project_id is None:
            raise BadRequest("This conversation was not placed in a project", field="links")
        to_decide = [link.id for link in waiting if link.project_id == placement.project_id]
        if not confirm and not to_decide:
            # Nothing waits: its links were answered already, and a rejection now would only
            # take the placement away from the project that holds them.
            raise Conflict("Nothing of this conversation waits for an answer")
        await projects.decide_links(db, actor, to_decide, confirm=bool(confirm))
        if not confirm:
            _answered(placement, Outcome.UNPLACED, None)
    await db.commit()
    return (await _describe(db, actor.company_id, [placement]))[0]


def _answered(placement: Placement, outcome: Outcome, project_id: uuid.UUID | None) -> None:
    """The owner's answer replaces the reading: what the linker said of it no longer holds."""
    placement.outcome = outcome
    placement.placed_by = BY_OWNER
    placement.project_id = project_id
    placement.alternatives = []
    placement.reason = ""
    placement.confidence = None
    placement.cited = []
    placement.reading = None
    placement.placed_at = utcnow()


async def _describe(
    db: AsyncSession, company_id: uuid.UUID, placements: Sequence[Placement]
) -> list[PlacementOut]:
    briefs = await conversations.thread_briefs(
        db, company_id, [placement.thread_id for placement in placements]
    )
    links = await projects.links_of_items(
        db,
        company_id,
        ItemKind.MESSAGE,
        [mail_id for brief in briefs.values() for mail_id in brief.mail_ids],
    )
    suggested = {
        (link.item_id, link.project_id) for link in links if link.status is LinkStatus.SUGGESTED
    }
    described = []
    for placement in placements:
        brief = briefs.get(placement.thread_id)
        described.append(
            PlacementOut(
                id=placement.id,
                thread_id=placement.thread_id,
                waiting=placement.waiting,
                outcome=placement.outcome,
                placed_by=placement.placed_by,
                project_id=placement.project_id,
                alternatives=list(placement.alternatives),
                reason=placement.reason,
                confidence=placement.confidence,
                cited=list(placement.cited),
                reading=(
                    {key: str(value) for key, value in placement.reading.items()}
                    if placement.reading
                    else None
                ),
                placed_at=placement.placed_at,
                suggested_links=sum(
                    (mail_id, placement.project_id) in suggested
                    for mail_id in (brief.mail_ids if brief is not None else [])
                ),
                conversation=brief.summary if brief is not None else None,
            )
        )
    return described
