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
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from pydantic_ai import UsageLimits
from pydantic_ai.messages import UserContent
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.models import InvoiceKind
from app.modules.books.schemas import InvoiceOut
from app.modules.companies import service as companies
from app.modules.conversations import service as conversations
from app.modules.conversations.models import ThreadCategory
from app.modules.conversations.service import MailRead, ThreadRead
from app.modules.documents import service as documents
from app.modules.documents.models import ReadState
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.modules.linking import agents, firms, repository
from app.modules.linking.models import Outcome, Placement
from app.modules.linking.schemas import JobReading, PlacementOut, PlacementPage
from app.modules.parties import service as parties
from app.modules.projects import service as projects
from app.modules.projects.models import (
    FieldSource,
    LinkMethod,
    LinkStatus,
    Project,
    ProjectLink,
    ProjectStatus,
)
from app.modules.projects.schemas import ProjectJob
from app.platform import events, llm
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import BadRequest, Conflict, NotFound
from app.platform.items import ItemKind
from app.platform.language import Language
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

# For the assistant's runs: the same searches the linker reads through, and the desk they keep
# what a run was shown on.
Desk = agents.Desk
reading_tools = agents.reading_tools
# A project's number as a run's desk keys it.
bare = agents.bare
# The kinds of record such a run opens (``Desk.opened``).
PROJECT = agents.PROJECT
CONVERSATION = agents.CONVERSATION

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
# New mail follows its conversation's project when it comes this soon after the last one, names
# nothing the project does not hold, and the project began no longer before it than
# FOLLOW_JOB_WITHIN. Else it is read afresh: a client often answers an old thread with the next
# order.
FOLLOW_WITHIN: Final = timedelta(days=14)
FOLLOW_JOB_WITHIN: Final = timedelta(days=21)
# A number the items sharing it carry over more days than this is a standing one: a client's
# blanket order, quoted on every load. It names no one job.
STANDING_OVER: Final = timedelta(days=30)
# A reply waits this long at most for its own files to be read before it may follow its
# conversation's project: a plate or a number may be only in them.
FILES_WAIT: Final = timedelta(days=1)
# The answer, the searches before it, and a retry when it fails the output's checks. A run that
# starts within the day's budget finishes, so a day can end this many reads past it.
_LIMITS: Final = UsageLimits(request_limit=12)
# A first answer less sure of itself than this is given to the linker's second model.
_SURE: Final = 0.6

# What a placement's ``placed_by`` holds when code placed it, and when the model gave no answer.
BY_KEYS: Final = "keys"
BY_THREAD: Final = "thread"
BY_OWNER: Final = "owner"
UNANSWERED: Final = "unanswered"
_KEY_CONFIDENCE: Final = 0.9
_THREAD_CONFIDENCE: Final = 0.6
_READ: Final = (ThreadCategory.WORK, ThreadCategory.AUTOMATED)
# A job's first and last day, in a reading and on a project.
_DAYS: Final = ("starts_on", "ends_on")
# The fields of a reading that name a firm, and what is no firm's name: a mail address, or a
# web domain standing alone.
_FIRMS: Final = ("client", "subcontractor", "consignee", "party")
# The roles a reading gives a firm in a job; a firm holds one of them in a project.
_ROLES: Final = ("client", "subcontractor")
_ADDRESS: Final = re.compile(r"\S+@\S+|(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}")


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
    # The project its last tied mails are in, when they run on to the end in it alone: the one
    # its next mail follows. None when its mails go back and forth between projects.
    latest: uuid.UUID | None = None


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
    # What its mails not tied yet say that the one project its others are tied to does not
    # hold, each in a few words: a number or a plate new to the job, or a first day long before
    # them. Each says they may be the next load.
    news: tuple[str, ...] = ()
    # When the oldest of those mails' files still to be read was stored; None when none waits.
    unread_since: datetime | None = None


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
        here = 0
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
            here += done
            if done:
                await events.publish_now(company_id, [events.EventKind.PROJECTS])
            async with open_session() as db:
                left = await conversations.model_reads_left(db, company_id)
        await _suggest_firms(company_id, placed=bool(await _place_bills(company_id) or here))
    # A company whose projects the owner made by hand has bills to place, and no conversation.
    async with open_session() as db:
        others = set(await projects.companies_with_projects(db)) - set(company_ids)
    for company_id in sorted(others):
        await _suggest_firms(company_id, placed=bool(await _place_bills(company_id)))
    return placed


async def _place_bills(company_id: uuid.UUID) -> int:
    """The company's bills placed after its conversations, which may have made the project a
    bill's numbers point to: how many were. A failure is logged and tried again next run."""
    try:
        async with open_session() as db:
            bills = await place_invoices(db, company_id)
            if bills.linked:
                await events.publish(db, company_id, events.EventKind.PROJECTS)
            await db.commit()
    except Exception as error:
        logger.warning("placing bills failed: %s", type(error).__name__)
        return 0
    if bills.linked:
        logger.info("bills placed in projects: %d", bills.linked)
    return bills.linked


async def _suggest_firms(company_id: uuid.UUID, *, placed: bool) -> None:
    """The firms signals name on the company's projects, after a run placed mails or bills there
    or the owner added, answered, moved or removed a link: a bill on a project by its number is
    one of them. Else nothing changed what names a firm, and nothing is read. A failure is
    logged and tried again next run."""
    async with open_session() as db:
        mark = await projects.links_mark(db, company_id)
    if not placed and _FIRMS_READ_AT.get(company_id) == mark:
        return
    try:
        async with open_session() as db:
            found = await firms.suggest_firms(db, company_id)
            if found.written:
                await events.publish(db, company_id, events.EventKind.PROJECTS)
            await db.commit()
    except Exception as error:
        logger.warning("suggesting firms failed: %s", type(error).__name__)
        return
    _FIRMS_READ_AT[company_id] = mark


# Each company's links as firms were last suggested from them (projects.links_mark), in this
# process: a worker started again suggests once for every company, then on a change.
_FIRMS_READ_AT: dict[uuid.UUID, tuple[int, datetime | None]] = {}


@dataclass(slots=True)
class BillsPlaced:
    linked: int = 0
    # Its numbers point to more than one project: the owner places it.
    several: int = 0
    # No project holds an item that shares a number with it.
    none: int = 0
    # A sale one project shares a number with, but whose client is another firm.
    contradicted: int = 0


async def place_invoices(db: AsyncSession, company_id: uuid.UUID) -> BillsPlaced:
    """Suggests each bill no project holds yet to the one project that holds an item sharing a
    key number with it: its number, its e-document id, an order or CMR number its description
    gives. A bill is placed once, as the record a project holds: an incoming e-invoice booked as a
    purchase is placed as that purchase, with what both of them say. A number more than
    KEY_MAX_ITEMS items share names no job, and a sale whose project has another client is
    counted, not placed: a number repeats across clients. Does not commit."""
    bills = {bill.id: bill for bill in await books.placeable_invoices(db, company_id)}
    records: dict[uuid.UUID, list[uuid.UUID]] = {}
    for bill in bills.values():
        records.setdefault(bill.same_as_id or bill.id, []).append(bill.id)
    held = {
        link.item_id
        for link in await projects.links_of_items(db, company_id, ItemKind.INVOICE, list(bills))
    }
    # An e-invoice a project held before it was booked: the project holds it as its purchase now.
    for target, ids in records.items():
        for alias in held & set(ids) - {target}:
            await projects.move_item_links(db, company_id, ItemKind.INVOICE, alias, target)
            held = (held - {alias}) | {target}
    # A bill whose booked record is cancelled, or one a project already holds as either record,
    # is not loose.
    loose = {
        target: ids
        for target, ids in records.items()
        if target in bills and not held & {target, *ids}
    }
    kinds = extractors.key_kinds()
    keys: dict[uuid.UUID, set[tuple[str, str]]] = {target: set() for target in loose}
    record_of = {each: target for target, ids in loose.items() for each in ids}
    for fact in await facts.facts_of_items(
        db, company_id=company_id, item_kind=ItemKind.INVOICE, item_ids=list(record_of)
    ):
        if fact.kind in kinds:
            keys[record_of[fact.item_id]].add((fact.kind, fact.normalised))
    for target, ids in loose.items():
        for each in ids:
            if document_id := bills[each].e_document_id:
                keys[target] |= {(kind, _as_compared_id(document_id)) for kind in kinds}
    same = await facts.items_sharing_each(
        db,
        company_id=company_id,
        pairs=sorted(set().union(*keys.values())),
        per_pair=KEY_MAX_ITEMS + 1 + max((len(ids) for ids in loose.values()), default=0),
    )
    standing = await _standing(
        db,
        company_id,
        {
            pair: {(fact.item_kind, fact.item_id) for fact in same[pair]}
            for pair in set().union(*keys.values())
        },
    )
    sharing: dict[uuid.UUID, dict[tuple[ItemKind, uuid.UUID], set[uuid.UUID]]] = {}
    for target, pairs in keys.items():
        mine = set(loose[target])
        for pair in pairs - standing:
            others = [
                fact
                for fact in same[pair]
                if not (fact.item_kind is ItemKind.INVOICE and fact.item_id in mine)
            ]
            if len(others) > KEY_MAX_ITEMS:
                continue
            for other in others:
                items = sharing.setdefault(target, {})
                items.setdefault((other.item_kind, other.item_id), set()).add(other.id)
    tied: dict[tuple[ItemKind, uuid.UUID], list[ProjectLink]] = {}
    for item_kind in (ItemKind.MESSAGE, ItemKind.DOCUMENT, ItemKind.INVOICE):
        ids = list(
            {item_id for items in sharing.values() for kind, item_id in items if kind is item_kind}
        )
        for link in await projects.links_of_items(db, company_id, item_kind, ids):
            if link.status is not LinkStatus.REJECTED:
                tied.setdefault((item_kind, link.item_id), []).append(link)
    known = await projects.get_projects(
        db, company_id, list({link.project_id for links in tied.values() for link in links})
    )
    firms = await projects.firms_of(db, company_id, list(known))
    placed = BillsPlaced()
    for target in loose:
        found: dict[uuid.UUID, set[uuid.UUID]] = {}
        for item, fact_ids in sharing.get(target, {}).items():
            for link in tied.get(item, []):
                if known[link.project_id].status is not ProjectStatus.REJECTED:
                    found.setdefault(link.project_id, set()).update(fact_ids)
        if len(found) > 1:
            placed.several += 1
        elif not found:
            placed.none += 1
        else:
            [(project_id, evidence)] = found.items()
            if _firm_differs(firms[project_id], bills[target]):
                placed.contradicted += 1
                continue
            await projects.link_item(
                db,
                company_id=company_id,
                item_kind=ItemKind.INVOICE,
                item_id=target,
                project_id=project_id,
                method=LinkMethod.KEY,
                confidence=_KEY_CONFIDENCE,
                evidence=sorted(evidence),
            )
            placed.linked += 1
    return placed


async def recheck_invoices(db: AsyncSession, company_id: uuid.UUID) -> int:
    """Removes the sales code placed by a shared number in a project whose client is another
    firm, as the rule of today would not place them; a link the owner answered stays. How many
    went. Does not commit."""
    links = await projects.suggested_links(db, company_id, ItemKind.INVOICE, LinkMethod.KEY)
    bills = {
        bill.id: bill
        for bill in await books.get_invoices(db, company_id, [link.item_id for link in links])
    }
    firms = await projects.firms_of(db, company_id, list({link.project_id for link in links}))
    wrong = [
        link.id
        for link in links
        if link.item_id in bills and _firm_differs(firms[link.project_id], bills[link.item_id])
    ]
    return await projects.drop_suggested(db, company_id, wrong)


def _firm_differs(firms: projects.ProjectFirms, bill: InvoiceOut) -> bool:
    """Whether a sale is billed to another firm than the project's client, where it is known. A
    purchase is never held against the job's carriers: a job buys from more firms than them (a
    customs agent, a port)."""
    return (
        bill.kind is InvoiceKind.SALES
        and firms.client.agree(bill.party_id, bill.party_name) is False
    )


def _as_compared_id(document_id: str) -> str:
    """An e-document id as facts hold one: a UUID in its canonical form, else lower case."""
    try:
        return str(uuid.UUID(document_id))
    except ValueError:
        return document_id.lower()


async def note_changes(company_id: uuid.UUID) -> None:
    """Marks as waiting every conversation that got mail or was sorted since the last look, and
    every one whose wait for its files is over."""
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
        await repository.wake_due(db, company_id, utcnow())
        await db.commit()


async def place_thread(
    company_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    model_allowed: bool,
    language: Language,
    trace_content: bool,
    upto: uuid.UUID | None = None,
) -> bool:
    """Places one conversation: by code when a rule decides, else by the ``linker`` agent when
    ``model_allowed``. Returns whether it was placed; one that needs the model and may not have
    it keeps waiting. No transaction is open while the model reads. ``upto`` places it as it
    stood once that mail of it had come, for a conversation placed again in the order its mail
    came (``recut_threads``); while later mails of it are left, it keeps waiting."""
    async with open_session() as db:
        placement = await repository.get(db, company_id, thread_id)
        if placement is None:
            return False
        seen_at = placement.placed_at
        placement.look_again_at = None
        thread = await conversations.read_thread(db, company_id, thread_id)
        left = False
        if thread is not None and upto is not None:
            sent = [mail.id for mail in thread.mails]
            cut = sent.index(upto) + 1 if upto in sent else len(sent)
            left = cut < len(sent)
            thread = replace(thread, mails=thread.mails[:cut])
        if thread is None or thread.category not in _READ or not thread.mails:
            # Sent back to be sorted, or sorted as noise since: nothing reads it until it changes.
            placement.waiting = False
            await db.commit()
            return False
        found = await _look(db, company_id, thread)
        if upto is None and found.unread_since and utcnow() - found.unread_since < FILES_WAIT:
            # Its new mails' files are read first: one can hold what its job does not. Reading
            # one notes the conversation changed, and it is looked at again then, or once the
            # wait is over if a file stays unread. Placed again in order, nothing waits: what is
            # not read by now is placed as it is.
            placement.waiting = False
            placement.look_again_at = found.unread_since + FILES_WAIT
            await db.commit()
            return False
        if (
            upto is None
            and placement.outcome is not None
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
                found.news,
            )
            desk = _desk(company_id, thread_id, found)
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
            decision = _from_model(run.output, desk, thread, found)
            if _contested(run.output, desk) and llm.second_model_name(agents.LINKER) is not None:
                decision = await _second_opinion(
                    decision,
                    prompt,
                    _desk(company_id, thread_id, found),
                    thread,
                    found,
                    usage=usage,
                    language=language,
                    trace_content=trace_content,
                )
        except llm.NoAnswer:
            decision = _Decision(Outcome.UNPLACED, UNANSWERED)
        except Exception:
            # The request that failed was sent the conversation too, after those that answered.
            await _count(company_id, usage.requests)
            raise
        await _count(company_id, usage.requests - 1)
    async with open_session() as db:
        applied = await _apply(db, company_id, thread, found, decision, seen_at=seen_at)
        if left:
            await repository.keep_waiting(db, company_id, [thread_id])
        await db.commit()
    return applied


@dataclass(slots=True)
class Recut:
    """What placing conversations again in the order their mail came does, or would do."""

    # Conversations whose later mails go back to be placed again.
    threads: int = 0
    mails: int = 0
    # Of those mails, the ones that name what their conversation's earlier mails did not, or
    # came long after them: each would send it to a model. An estimate: where one becomes the
    # next load, the mails after it are measured against that load instead.
    news: int = 0
    # Written only: mails placed again, model reads made, and replays that failed. A mail left
    # unplaced keeps its conversation waiting, for the worker to place within the day's budget.
    placed: int = 0
    reads: int = 0
    failed: int = 0


async def recut_threads(company_id: uuid.UUID, *, write: bool, max_reads: int = 0) -> Recut:
    """Places again, under the rules of today, the mails each conversation brought after its
    first into the one project code tied it to: a thread with daily replies often carried the
    next loads into its first project. Its first mail stays; the others are untied, with the
    files they carried, and placed again one by one in the order they were sent, across every
    conversation, as they would have been had they come today: code follows where nothing is
    new, the linker reads where a mail names what its job does not hold, starting no model run
    once ``max_reads`` requests were made (one under way may go past it, by a run's requests at
    most). A conversation the owner tied or answered anywhere, or one in more than one
    project, stays as it is. Each conversation untied waits until its last mail is placed
    again: stopped half way, or short of reads, the worker places the rest. Run it with the
    worker stopped. A dry run counts and writes nothing; no model is asked in it."""
    recut = Recut()
    later: list[tuple[datetime, uuid.UUID, uuid.UUID]] = []
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id]))[company_id]
        language = company.language
        trace_content = companies.traces_content(company.kind)
        for placement in await repository.placed_in_projects(db, company_id):
            if placement.placed_by == BY_OWNER:
                continue
            thread = await conversations.read_thread(db, company_id, placement.thread_id)
            # One sorted as noise since, or sent back to be sorted, is placed by nothing now.
            if thread is None or thread.category not in _READ or len(thread.mails) < 2:
                continue
            links = await projects.links_of_items(
                db, company_id, ItemKind.MESSAGE, [mail.id for mail in thread.mails]
            )
            held = {link.project_id for link in links}
            if any(link.method is LinkMethod.OWNER or link.decided_by for link in links) or (
                len(held) != 1
            ):
                continue
            # The owner rejected the project it is in: what they refused is not proposed again.
            [project] = (await projects.get_projects(db, company_id, list(held))).values()
            if project.status is ProjectStatus.REJECTED:
                continue
            tied = {link.item_id: link for link in links}
            again = [mail for mail in thread.mails[1:] if mail.id in tied]
            if not again:
                continue
            recut.threads += 1
            recut.mails += len(again)
            recut.news += await _new_in_order(db, company_id, thread.mails)
            later.extend((mail.sent_at, mail.id, thread.id) for mail in again)
            if write:
                await projects.drop_suggested(db, company_id, [tied[m.id].id for m in again])
                await projects.carry_files(db, company_id, [mail.id for mail in again])
        if write:
            await repository.keep_waiting(db, company_id, list({each[2] for each in later}))
        await (db.commit() if write else db.rollback())
    if not write:
        return recut
    began = utcnow()
    failed: set[uuid.UUID] = set()
    for _, mail_id, thread_id in sorted(later):
        if thread_id in failed:
            # Its later mails are left to the worker with the one that failed, not placed with it.
            continue
        async with open_session() as db:
            recut.reads = await conversations.model_reads_since(db, company_id, began)
        try:
            recut.placed += await place_thread(
                company_id,
                thread_id,
                model_allowed=recut.reads < max_reads,
                language=language,
                trace_content=trace_content,
                upto=mail_id,
            )
        except Exception as error:  # one that fails stays untied, its conversation waiting
            logger.warning("placing again %s failed: %s", thread_id, type(error).__name__)
            recut.failed += 1
            failed.add(thread_id)
    async with open_session() as db:
        recut.reads = await conversations.model_reads_since(db, company_id, began)
        await tell_projects_again(db, company_id)
        await db.commit()
    await events.publish_now(company_id, [events.EventKind.PROJECTS])
    return recut


async def _new_in_order(db: AsyncSession, company_id: uuid.UUID, mails: Sequence[MailRead]) -> int:
    """How many of a conversation's mails after its first name a key number or a plate none
    before them did, or came more than FOLLOW_WITHIN after the one before or FOLLOW_JOB_WITHIN
    after its first: what would send each to a model when it came."""
    kinds = extractors.load_kinds()
    said: dict[uuid.UUID, set[tuple[str, str]]] = {mail.id: set() for mail in mails}
    files = {file.document_id: mail.id for mail in mails for file in mail.files}
    for item_kind, ids in ((ItemKind.MESSAGE, list(said)), (ItemKind.DOCUMENT, list(files))):
        for fact in await facts.facts_of_items(
            db, company_id=company_id, item_kind=item_kind, item_ids=ids
        ):
            if fact.kind in kinds:
                owner = fact.item_id if item_kind is ItemKind.MESSAGE else files[fact.item_id]
                said[owner].add((fact.kind, fact.normalised))
    seen = set(said[mails[0].id])
    new = 0
    for before, mail in zip(mails, mails[1:], strict=False):
        late = (
            mail.sent_at - before.sent_at > FOLLOW_WITHIN
            or mail.sent_at - mails[0].sent_at > FOLLOW_JOB_WITHIN
        )
        new += late or bool(said[mail.id] - seen)
        seen |= said[mail.id]
    return new


def _desk(company_id: uuid.UUID, thread_id: uuid.UUID, found: _Found) -> agents.Desk:
    """What a run of the linker starts from: the projects code already ties the conversation to,
    and those the owner refused for it."""
    ties = found.ties
    desk = agents.Desk(
        company_id=company_id,
        thread_id=thread_id,
        refused={agents.bare(found.numbers[project_id]) for project_id in ties.refused},
    )
    for project_id in (*ties.held, *found.keys):
        desk.show(found.numbers[project_id], project_id)
    return desk


def _contested(placing: agents.Placing, desk: agents.Desk) -> bool:
    """Whether a first answer is one for the agent's second model: it places the conversation
    though another answer was open. A link while the run saw another project too (the same
    client's other job), a new project while it saw any (the same job, proposed twice), two that
    fit equally, or an answer its own writer was not sure of."""
    if placing.decision == "unplaced":
        return False
    others = len(desk.projects) - (1 if placing.decision == "link" else 0)
    return others > 0 or placing.confidence < _SURE


async def _second_opinion(
    first: _Decision,
    prompt: Sequence[UserContent],
    desk: agents.Desk,
    thread: ThreadRead,
    found: _Found,
    *,
    usage: RunUsage,
    language: Language,
    trace_content: bool,
) -> _Decision:
    """The linker's second model on the same conversation, from the start and without the first
    answer: what it decides is the decision. When it gives none, or cannot be reached, the first
    stands: a second look that fails never holds back an answer already there."""
    try:
        run = await llm.run(
            agents.linker,
            prompt,
            deps=desk,
            limits=_LIMITS,
            language=language,
            trace_content=trace_content,
            usage=usage,
            second=True,
        )
    except Exception as error:
        # The error's type only: a provider's error can quote the mail it was sent.
        logger.warning(
            "the second opinion on conversation %s failed: %s", thread.id, type(error).__name__
        )
        return first
    return _from_model(run.output, desk, thread, found)


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
    following = ties.latest if ties.untied else None
    news = await _news(db, company_id, thread, ties, known[following]) if following else ()
    unread = [
        files[file.document_id].created_at
        for mail in thread.mails
        if mail.id in ties.untied
        for file in mail.files
        if file.document_id in files and files[file.document_id].read_state is ReadState.PENDING
    ]
    return _Found(
        news=news,
        unread_since=min(unread) if following and unread else None,
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


async def _news(
    db: AsyncSession,
    company_id: uuid.UUID,
    thread: ThreadRead,
    ties: _Ties,
    project: Project,
) -> tuple[str, ...]:
    """What the conversation's mails not tied yet name that the project its last mails are in
    does not hold (a key number, a plate), and whether that project began long before them."""
    kinds = extractors.load_kinds()
    untied = [mail for mail in thread.mails if mail.id in ties.untied]
    said = [
        *await facts.facts_of_items(
            db, company_id=company_id, item_kind=ItemKind.MESSAGE, item_ids=[m.id for m in untied]
        ),
        *await facts.facts_of_items(
            db,
            company_id=company_id,
            item_kind=ItemKind.DOCUMENT,
            item_ids=[file.document_id for mail in untied for file in mail.files],
        ),
    ]
    links = [
        link
        for link in await projects.links_of_project(db, company_id, project.id)
        if link.status is not LinkStatus.REJECTED
    ]
    held: set[tuple[str, str]] = set()
    for item_kind in (ItemKind.MESSAGE, ItemKind.DOCUMENT, ItemKind.INVOICE):
        ids = [link.item_id for link in links if link.item_kind is item_kind]
        held |= {
            (fact.kind, fact.normalised)
            for fact in await facts.facts_of_items(
                db, company_id=company_id, item_kind=item_kind, item_ids=ids
            )
            if fact.kind in kinds
        }
    new: dict[tuple[str, str], str] = {}
    for fact in said:
        if fact.kind in kinds and (fact.kind, fact.normalised) not in held:
            new.setdefault(
                (fact.kind, fact.normalised), f"{fact.kind.replace('_', ' ')} {fact.value}"
            )
    news = sorted(new.values())
    first, since = project.starts_on, min(mail.sent_at for mail in untied).date()
    if first is not None and since - first > FOLLOW_JOB_WITHIN:
        news.append(
            f"the project's first day, {first.isoformat()}, {(since - first).days} days before them"
        )
    return tuple(news)


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
        latest=_latest(thread, tied),
    )


def _latest(thread: ThreadRead, tied: Mapping[uuid.UUID, set[uuid.UUID]]) -> uuid.UUID | None:
    """The project a conversation's last tied mails are in, where every tied mail from its first
    one there on is in it alone: a conversation that carried the next load to another project
    goes on in that one. None where its mails go back and forth (it carries two jobs at once),
    or where a mail not tied yet came before that project's first while another's mails did too:
    an older mail read late from the mailbox may be the earlier job's."""
    of: dict[uuid.UUID, set[uuid.UUID]] = {}
    for project_id, mail_ids in tied.items():
        for mail_id in mail_ids:
            of.setdefault(mail_id, set()).add(project_id)
    sent = [mail.id for mail in thread.mails]
    runs = [(index, of[mail_id]) for index, mail_id in enumerate(sent) if mail_id in of]
    if not runs or len(runs[-1][1]) != 1:
        return None
    [latest] = runs[-1][1]
    start = next(index for index, held in runs if latest in held)
    if any(held != runs[-1][1] for index, held in runs if index >= start):
        return None
    if runs[0][0] < start and any(mail_id not in of for mail_id in sent[:start]):
        return None
    return latest


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
    carrying: dict[tuple[str, str], set[_Item]] = {}
    # A file's own key facts: the same file in another mail is the same paper of the same job.
    on_file: dict[uuid.UUID, set[uuid.UUID]] = {}
    for item_kind, ids in ((ItemKind.MESSAGE, mail_ids), (ItemKind.DOCUMENT, file_ids)):
        for fact in await facts.facts_of_items(
            db, company_id=company_id, item_kind=item_kind, item_ids=ids
        ):
            if fact.kind in kinds:
                carried.setdefault((fact.kind, fact.normalised), set()).add(fact.id)
                carrying.setdefault((fact.kind, fact.normalised), set()).add(
                    (item_kind, fact.item_id)
                )
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
    standing = await _standing(
        db,
        company_id,
        {
            pair: carrying[pair] | {(fact.item_kind, fact.item_id) for fact in same[pair]}
            for pair in looked_up
        },
    )
    for pair, fact_ids in looked_up.items():
        others = [fact for fact in same[pair] if (fact.item_kind, fact.item_id) not in own]
        if len(others) > KEY_MAX_ITEMS or pair in standing:
            continue
        for other in others:
            sharing.setdefault((other.item_kind, other.item_id), set()).update(fact_ids)
    # A file is stored once however many mails carry it: one of this conversation's files that
    # holds a key is looked for in the other mails that carried it, and in its own links.
    for file_id, fact_ids in on_file.items():
        sharing.setdefault((ItemKind.DOCUMENT, file_id), set()).update(fact_ids)
    # The books hold an invoice under its number or its e-document id, not as a fact.
    keys = {pair: fact_ids for pair, fact_ids in looked_up.items() if pair not in standing}
    for invoice in await books.invoices_named(
        db,
        company_id,
        numbers=[value for kind, value in keys if kind == extractors.INVOICE_NUMBER],
        e_document_ids=[value.lower() for _, value in keys],
    ):
        names = {extractors.reference(invoice.number or ""), (invoice.e_document_id or "").lower()}
        for (_, normalised), fact_ids in keys.items():
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


type _Item = tuple[ItemKind, uuid.UUID]


async def _standing(
    db: AsyncSession, company_id: uuid.UUID, carried: Mapping[tuple[str, str], set[_Item]]
) -> set[tuple[str, str]]:
    """The numbers among these that their mails and files carry over more than STANDING_OVER: a
    client's blanket order, quoted on every load, names no one job. A file is dated by the first
    mail that brought it."""
    items = set().union(*carried.values()) if carried else set()
    mails = [item_id for kind, item_id in items if kind is ItemKind.MESSAGE]
    files = [item_id for kind, item_id in items if kind is ItemKind.DOCUMENT]
    days: dict[_Item, date] = {
        (ItemKind.MESSAGE, mail_id): mail.sent_at.date()
        for mail_id, mail in (await conversations.message_summaries(db, company_id, mails)).items()
    }
    days |= {
        (ItemKind.DOCUMENT, file_id): carrier.sent_at.date()
        for file_id, carrier in (await conversations.first_carriers(db, company_id, files)).items()
    }
    standing = set()
    for pair, carriers in carried.items():
        dated = [days[item] for item in carriers if item in days]
        if dated and max(dated) - min(dated) > STANDING_OVER:
            standing.add(pair)
    return standing


def _by_code(thread: ThreadRead, placement: Placement, found: _Found) -> _Decision | None:
    """What code can decide without a model, or None when a model has to read it."""
    ties = found.ties
    # Nothing of it is unplaced (the owner, or an earlier run, tied every mail), or every mail
    # that is came soon before or after one that is tied and names nothing new to the job its
    # last mails are in. A system's notice says nothing of a next load.
    if ties.latest is not None and (ties.gap is None or ties.gap <= FOLLOW_WITHIN):
        if not found.news or thread.category is ThreadCategory.AUTOMATED:
            return _follows(ties.latest, found, placement)
        # The new mails may be the next load: a model reads them.
        return None
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
        reading=placement.reading if placement.project_id == project_id else None,
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
    # A role says nothing without the firm it is the role of.
    party = (
        {"party": placing.party, "party_role": placing.party_role}
        if placing.party and placing.party.strip() and placing.party_role
        else {}
    )
    if placing.decision == "link":
        project_id = desk.projects.get(agents.bare(placing.project or ""))
        if project_id is None or project_id in found.ties.refused:
            return unplaced
        said = placing.job.model_dump(exclude_none=True) if placing.job else {}
        return _Decision(
            Outcome.LINKED,
            agents.MODEL_PLACED,
            project_id=project_id,
            confidence=placing.confidence,
            evidence=found.keys.get(project_id, frozenset()),
            reason=reason,
            cited=cited,
            reading=_as_read(said | party),
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
        job = placing.new_project.model_dump(exclude={"name"}, exclude_none=True)
        title = _title(_as_read(job) or {})
        # The model's own words name the project only when the job does not, and an address
        # is no more a project's name than a firm's.
        if not title and (not name or _ADDRESS.fullmatch(name)):
            return unplaced
        return _Decision(
            Outcome.PROPOSED,
            agents.MODEL_PLACED,
            new_name=title or name,
            confidence=placing.confidence,
            reason=reason,
            cited=cited,
            reading=_as_read(job | party),
        )
    return unplaced


def _town(place: str | None) -> str:
    """A place as a name holds it: "Novi Sad, Serbia" is Novi Sad."""
    return (place or "").split(",")[0].strip()[:60]


def _day(read: Mapping[str, Any], field: str) -> date | None:
    try:
        return date.fromisoformat(str(read[field])) if read.get(field) else None
    except ValueError:
        return None


def _title(job: Mapping[str, Any]) -> str | None:
    """A project's name from what is known of its job: the client, the route and the first day,
    the same way for every project ("Stone GmbH · Izmir → Hamburg · 05.10.2026"). None when
    neither the client nor both ends of the route are known: the model's few words name it."""
    client = parties.short_name(str(job.get("client") or ""))[:120]
    origin, destination = _town(job.get("origin")), _town(job.get("destination"))
    if not client and not (origin and destination):
        return None
    route = f"{origin} → {destination}".strip() if origin or destination else ""
    first = _day(job, "starts_on")
    parts = [client, route, f"{first:%d.%m.%Y}" if first else ""]
    return " · ".join(part for part in parts if part)[:300]


def _known(readings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What a project's conversations say of its job, together. Each field is what most of them
    say, a tie going to the one noted last, so one conversation that misread the job does not
    name it. A firm read as the client in some and as the subcontractor in others holds only the
    role most of them give it. The days are the earliest start and the latest end."""
    known: dict[str, Any] = {}
    for field in ("consignee", "cargo", "trucks"):
        if found := _ranked([reading.get(field) for reading in readings], _same):
            known[field] = found[0]
    for field in ("origin", "destination"):
        places = [reading.get(field) for reading in readings]
        if found := _ranked(places, lambda place: _same(_town(place))):
            known[field] = found[0]
    votes, held = _firms(readings)
    firms = {
        role: _ranked([f for f, said in votes if said == role and held[_same(f)] == role], _same)
        for role in _ROLES
    }
    if firms["client"]:
        known["client"] = firms["client"][0]
    if firms["subcontractor"]:
        known["subcontractors"] = firms["subcontractor"]
    starts = [day for reading in readings if (day := _day(reading, "starts_on"))]
    ends = [day for reading in readings if (day := _day(reading, "ends_on"))]
    if starts:
        known["starts_on"] = min(starts).isoformat()
    if ends and (not starts or max(ends) >= min(starts)):
        known["ends_on"] = max(ends).isoformat()
    return known


def _firms(readings: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Each firm the readings name as the client or the subcontractor, once per reading and role,
    in order; and the one role each firm holds, by its name's key: the one most readings give it,
    a tie going to the reading noted last. The firm a conversation deals with (``party``) counts
    in the role the reading gives it."""
    votes: list[tuple[str, str]] = []
    for reading in readings:
        named = [(reading.get(role), role) for role in _ROLES]
        if reading.get("party_role") in _ROLES:
            named.append((reading.get("party"), reading["party_role"]))
        seen: set[tuple[str, str]] = set()
        for firm, role in named:
            if firm and _same(firm) and (_same(firm), role) not in seen:
                seen.add((_same(firm), role))
                votes.append((str(firm), role))
    roles: dict[str, list[str]] = {}
    for firm, role in votes:
        roles.setdefault(_same(firm), []).append(role)
    return votes, {firm: _ranked(said, str)[0] for firm, said in roles.items()}


def _ranked(values: Sequence[Any], key: Callable[[Any], str]) -> list[Any]:
    """The distinct values by ``key``, the one said most first, a tie going to the one said
    last. Each is given in the spelling said most among its own, a tie again going to the last:
    "London, UK" twice and "London, Canada" once are one town, and it is London, UK."""
    said: dict[str, dict[Any, tuple[int, int]]] = {}
    for at, value in enumerate(values):
        if value is None or not (same := key(value)):
            continue
        spellings = said.setdefault(same, {})
        count, _ = spellings.get(value, (0, at))
        spellings[value] = (count + 1, at)

    def weight(spellings: dict[Any, tuple[int, int]]) -> tuple[int, int]:
        return sum(count for count, _ in spellings.values()), max(
            at for _, at in spellings.values()
        )

    order = sorted(said.values(), key=weight, reverse=True)
    return [max(spellings, key=lambda value: spellings[value]) for spellings in order]


def _same(value: Any) -> str:
    """The form two spellings of one value share: "ATLAS NAKLİYAT LTD. ŞTİ." and "Atlas
    Nakliyat" are one firm."""
    return parties.name_key(str(value))


def _job(known: Mapping[str, Any]) -> ProjectJob | None:
    """What is known of a project's job, as the project shows it; None when nothing is."""
    trucks = str(known.get("trucks") or "")
    job = ProjectJob(
        client=parties.short_name(known["client"]) if known.get("client") else None,
        subcontractors=[parties.short_name(firm) for firm in known.get("subcontractors", [])],
        consignee=parties.short_name(known["consignee"]) if known.get("consignee") else None,
        origin=known.get("origin"),
        destination=known.get("destination"),
        cargo=known.get("cargo"),
        trucks=int(trucks) if trucks.isdigit() else None,
    )
    return None if job == ProjectJob() else job


async def _readings(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """What was read in each conversation placed in each project, with what a conversation whose
    later mails went to another project read there while it still has mail in it, in the order
    the conversations were first noted."""
    readings = await repository.readings_of_projects(db, company_id, project_ids)
    kept = await repository.kept_readings(db, company_id, project_ids)
    if kept:
        links = await projects.links_of_projects(
            db, company_id, list({row.project_id for row in kept})
        )
        threads = await conversations.thread_ids_of(
            db, company_id, [link.item_id for link in links if link.item_kind is ItemKind.MESSAGE]
        )
        holding = {
            (link.project_id, threads[link.item_id]) for link in links if link.item_id in threads
        }
        for row in kept:
            if (row.project_id, row.thread_id) in holding:
                readings.setdefault(row.project_id, []).append((row.noted, row.reading))
    return {
        project_id: [reading for _, reading in sorted(read, key=lambda each: each[0])]
        for project_id, read in readings.items()
    }


async def _jobs(
    db: AsyncSession, company_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ProjectJob]:
    """Each project's job as its conversations describe it together, for the projects shown."""
    readings = await _readings(db, company_id, project_ids)
    return {
        project_id: job
        for project_id, read in readings.items()
        if (job := _job(_known(read))) is not None
    }


projects.read_jobs_with(_jobs)


def _as_read(read: dict[str, Any]) -> dict[str, str] | None:
    """What a model read, as it is kept: one line a value, the empty ones left out. A firm
    given only as a mail address or a web domain is left out too: that is where a mail came
    from, not who the firm is, and a project would be named by it."""
    kept = {
        key: " ".join(str(value).replace("\x00", "").split())[:300] for key, value in read.items()
    }
    for firm in _FIRMS:
        if _ADDRESS.fullmatch(kept.get(firm, "")):
            del kept[firm]
    if "party" not in kept:
        kept.pop("party_role", None)
    return {key: value for key, value in kept.items() if value} or None


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
    said = decision.reading or {}
    if decision.new_name:
        first, last = _day(said, "starts_on"), _day(said, "ends_on")
        project = await projects.new_project(
            db,
            company_id=company_id,
            name=decision.new_name,
            status=ProjectStatus.PROPOSED,
            source=FieldSource.SUGGESTION,
            starts_on=first,
            ends_on=last if last and (not first or last >= first) else None,
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
        # The files its mails carried are suggested with them.
        await projects.carry_files(db, company_id, sorted(ties.untied))
    # Its later mails gone to another project, the conversation's earlier ones stay in the one it
    # was in, and so does what they said of the job there.
    if (
        placement.project_id is not None
        and project_id not in (None, placement.project_id)
        and placement.project_id in ties.held
        and placement.reading
    ):
        await repository.keep_reading(
            db, company_id, thread.id, placement.project_id, placement.reading
        )
    # Read again and left in its project, a conversation keeps what was read of the job before.
    stays = project_id is not None and project_id == placement.project_id
    reading = (
        {**(placement.reading or {}), **(decision.reading or {})} if stays else decision.reading
    )
    placement.outcome = decision.outcome
    placement.placed_by = decision.placed_by
    placement.project_id = project_id
    placement.alternatives = alternatives
    placement.reason = decision.reason
    placement.confidence = decision.confidence
    placement.cited = list(decision.cited)
    placement.reading = reading or None
    placement.mails = len(thread.mails)
    placement.last_mail_at = thread.mails[-1].sent_at
    placement.files_read = found.files_read
    placement.placed_at = utcnow()
    placement.failures = 0
    placement.failed_at = None
    # Mail that came while it was read is a newer change than the one read: it waits again.
    placement.waiting = placement.thread_changed_at > thread.changed_at
    if placement.placed_by == agents.MODEL_PLACED and project_id is not None and reading:
        await db.flush()
        await _tell_project(db, company_id, project_id, said=said, evidence=decision.cited)
    return True


async def _tell_project(
    db: AsyncSession,
    company_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    said: Mapping[str, Any] | None,
    evidence: Sequence[uuid.UUID],
) -> None:
    """Brings a project up to what its conversations say of the job together: its days, and its
    name while nobody but Tamga named it. ``said`` is what the conversation just placed says: a
    day it states is written, or proposed when the owner set that day, with ``evidence`` as its
    proof. With ``said`` None a conversation has left the project (the owner's answer): what
    Tamga derived is worked out again from those that remain, and nothing the owner set is
    touched or proposed over."""
    readings = await _readings(db, company_id, [project_id])
    known = _known(readings.get(project_id, []))
    values: dict[str, Any] = {}
    name = _title(known)
    if name:
        values["name"] = name
    for field in _DAYS:
        if said is None:
            values[field] = known.get(field)
        elif field in said and field in known:
            values[field] = known[field]
    if values:
        await projects.propose_project_fields(
            db,
            company_id=company_id,
            project_id=project_id,
            values=values,
            source=FieldSource.SUGGESTION,
            evidence=evidence,
            leave_owner=("name",) if said is not None else ("name", *_DAYS),
        )


async def tell_projects_again(
    db: AsyncSession, company_id: uuid.UUID
) -> list[tuple[str, str, str]]:
    """Works out again, from what its conversations say, the name and days Tamga gave each of the
    company's projects: for when the way they are worked out changed. The owner's own are left
    as they are. Returns the number, the old name and the new one of each project renamed. Does
    not commit."""
    placed = await repository.read_projects(db, company_id)
    before = await projects.get_projects(db, company_id, placed)
    names = {project_id: project.name for project_id, project in before.items()}
    for project_id in placed:
        await _tell_project(db, company_id, project_id, said=None, evidence=())
    await db.flush()
    after = await projects.get_projects(db, company_id, placed)
    return sorted(
        (project.number, names[project_id], project.name)
        for project_id, project in after.items()
        if project.name != names[project_id]
    )


# What the owner sees and answers


async def companies_awaiting_owner(db: AsyncSession) -> list[uuid.UUID]:
    """The companies with a proposed project or a conversation the linker could not choose a
    project for."""
    return await repository.companies_awaiting_owner(db)


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


async def placement_of(
    db: AsyncSession, company_id: uuid.UUID, thread_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    """A conversation's placement and the project it is in, if the linker placed it."""
    placement = await repository.get(db, company_id, thread_id)
    return (placement.id, placement.project_id) if placement is not None else None


async def get_placement(db: AsyncSession, actor: Actor, placement_id: uuid.UUID) -> PlacementOut:
    placement = await repository.get_by_id(db, actor.company_id, placement_id)
    if placement is None:
        raise NotFound("No such placement")
    return (await _describe(db, actor.company_id, [placement]))[0]


async def decide_placement(
    db: AsyncSession,
    actor: Actor,
    placement_id: uuid.UUID,
    *,
    confirm: bool | None,
    project_id: uuid.UUID | None,
    moving_from: uuid.UUID | None = None,
) -> PlacementOut:
    """The owner's answer for a whole conversation. ``confirm``: its links that wait, to the
    project it was placed in, are confirmed or rejected; rejected, it is unplaced until new mail
    has it read again. ``project_id``: the owner names its project: every mail of it that no
    confirmed link holds is tied there, and what was suggested elsewhere is rejected. With
    ``moving_from``, the project the owner saw it in, the conversation is moved: its mails
    confirmed there go too. Without it, a conversation confirmed elsewhere is not taken away by
    an answer given from a page that no longer shows where it is."""
    actor.require(Role.OWNER)
    placement = await repository.get_by_id(db, actor.company_id, placement_id, for_update=True)
    if placement is None:
        raise NotFound("No such placement")
    was = placement.project_id
    thread = await conversations.read_thread(db, actor.company_id, placement.thread_id)
    mail_ids = [mail.id for mail in thread.mails] if thread is not None else []
    links = await projects.links_of_items(db, actor.company_id, ItemKind.MESSAGE, mail_ids)
    waiting = [link for link in links if link.status is LinkStatus.SUGGESTED]
    if project_id is not None:
        chosen = (await projects.get_projects(db, actor.company_id, [project_id])).get(project_id)
        if chosen is None or chosen.status is ProjectStatus.REJECTED:
            raise NotFound("No such project", field="project_id")
        if moving_from is not None and moving_from != was:
            # Moved or answered since the owner looked, in another tab perhaps.
            raise Conflict("This conversation is no longer in that project")
        # Moved out of the project it is in, what the owner confirmed there is taken back with
        # it. A mail confirmed in a third project stays there: an old thread answered with a new
        # order holds two jobs.
        leaving = [
            link
            for link in links
            if link.status is LinkStatus.CONFIRMED
            and link.project_id == moving_from
            and moving_from != project_id
        ]
        confirmed = [link for link in links if link.status is LinkStatus.CONFIRMED]
        held = {link.item_id for link in confirmed if link not in leaving}
        free = [mail_id for mail_id in mail_ids if mail_id not in held]
        if not free and all(link.project_id != project_id for link in confirmed):
            # Answered before, in another tab perhaps: every mail is confirmed elsewhere, and
            # saying it is here would leave the placement and its links apart.
            raise Conflict("This conversation is already confirmed in another project")
        await projects.decide_links(
            db,
            actor,
            [link.id for link in (*waiting, *leaving) if link.project_id != project_id],
            confirm=False,
            withdraw=True,
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
        # What the mail says of its job holds whichever project it is in.
        said = placement.reading
        _answered(placement, Outcome.LINKED, project_id)
        placement.reading = said
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
    # Its files follow the answer: decide_links carried those of the mails it decided, and the
    # mails tied here now carry theirs.
    await projects.carry_files(db, actor.company_id, mail_ids)
    if was != placement.project_id:
        # It left one project, joined another, or both: what was read in it no longer names or
        # dates the first, and does the second.
        await db.flush()
        for changed in (was, placement.project_id):
            if changed is not None:
                await _tell_project(db, actor.company_id, changed, said=None, evidence=())
    await db.commit()
    return (await _describe(db, actor.company_id, [placement]))[0]


async def _merged(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """One project went into another (``projects.merge_project``): the conversations placed in
    the first are in the second, a question between the two is answered, and the name and days
    Tamga derived for the one that stays are worked out again from all its conversations. Runs
    in the merge's transaction and does not commit."""
    await repository.move_kept_readings(db, company_id, source_id, target_id)
    for placement in await repository.naming(db, company_id, source_id):
        # A run that read the conversation before the merge decided on what is gone: stamped
        # anew, its decision is dropped and the conversation looked at afresh (``_apply``).
        placement.placed_at = utcnow()
        if placement.project_id == source_id:
            placement.project_id = target_id
            if placement.outcome is Outcome.PROPOSED:
                # The project proposed for it is gone: it is one of the other's conversations.
                placement.outcome = Outcome.LINKED
        renamed = (target_id if each == source_id else each for each in placement.alternatives)
        fits = list(dict.fromkeys(renamed))
        if placement.outcome is Outcome.ASKED and len(fits) < 2:
            # The two it stood between are one: its mails are suggested there, for the owner.
            thread = await conversations.read_thread(db, company_id, placement.thread_id)
            ties = await _ties(db, company_id, thread) if thread is not None else None
            for mail_id in sorted(ties.untied) if ties is not None else ():
                await projects.link_item(
                    db,
                    company_id=company_id,
                    item_kind=ItemKind.MESSAGE,
                    item_id=mail_id,
                    project_id=target_id,
                    method=LinkMethod.AGENT,
                    confidence=placement.confidence or 0.0,
                )
            # Tied elsewhere since it was asked about (by hand, mail by mail), it is in neither.
            there = ties is not None and (ties.untied or target_id in ties.held)
            placement.outcome = Outcome.LINKED if there else Outcome.UNPLACED
            placement.project_id, fits = (target_id if there else None), []
        placement.alternatives = fits
    await db.flush()
    await _tell_project(db, company_id, target_id, said=None, evidence=())


projects.on_merge(_merged)


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
                reading=JobReading.model_validate(placement.reading) if placement.reading else None,
                placed_at=placement.placed_at,
                suggested_links=sum(
                    (mail_id, placement.project_id) in suggested
                    for mail_id in (brief.mail_ids if brief is not None else [])
                ),
                conversation=brief.summary if brief is not None else None,
            )
        )
    return described
