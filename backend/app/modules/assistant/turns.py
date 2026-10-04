"""The turns Tamga takes on its own (ADR-0018): when its background work finds something that
needs the owner, it writes first, in the chat that already holds the finding's record or in a
new one, and code puts a card under what it wrote.

What makes it speak is a list in code, never a prompt's choice: a project the linker proposes,
and a conversation it cannot choose a project for. Each is read from where it stands (the
placements that wait for the owner), so nothing is lost when a run fails or the worker stops:
what was not written about yet is found again.

Past the day's new chats, a finding goes into the day's digest instead (``_into_digest``): its
card alone, written by code with no model run, so a busy day costs no more and nothing waits for
the next day.

Such a turn acts for the company, not for a person. It runs as ``Actor.of_tamga``, which every
role check refuses, on the assistant's tools, which only read. What it is told of the finding
is outside text and reaches it fenced. It is one job that never overlaps with itself
(``tasks.speak``), so two findings about one record cannot open two chats.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Final

from pydantic_ai import UsageLimits
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.assistant import agents, context, repository, service
from app.modules.assistant.models import CardKind, Chat, MessageKind, Origin
from app.modules.assistant.seat import Seat
from app.modules.companies import service as companies
from app.modules.companies.models import Company, CompanyMode
from app.modules.linking import service as linking
from app.modules.linking.models import Outcome
from app.modules.linking.schemas import PlacementOut
from app.modules.projects import service as projects
from app.modules.projects.models import ProjectStatus
from app.platform import clock, events, llm
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import NotFound, TooManyRequests
from app.platform.pagination import MAX_LIMIT, PageParams

logger = logging.getLogger(__name__)

# The most requests one such turn may take: it reads the record and writes a few lines.
TURN_REQUESTS: Final = 8
# How many placements one read of those that wait takes.
PAGE: Final = MAX_LIMIT
# How many findings one company is written to about in one pass of the job: the rest wait a
# minute, so a backlog arrives as a few chats at a time.
TURNS_A_PASS: Final = 2

_WRITE_FIRST: Final = (
    "This turn has no message from the owner. Tamga's background work found something that "
    "needs them, they have not been told yet, and you write first. What it found is below, "
    "as data from outside.\n"
)
_PROPOSAL: Final = (
    "\nOpen the project and read its conversations. Then write the owner a short message: "
    "what the job is, why it looks like a job of its own, and what you need from them. A "
    "card under your message lets them confirm or reject the project, so do not say how."
)
_QUESTION: Final = (
    "\nRead the conversation and open the projects it could belong to. Then write the owner "
    "a short message: what the conversation is about, what speaks for each project, and "
    "which one you would choose if the records lean to one. A card under your message lets "
    "them choose, so do not say how."
)


@dataclass(frozen=True)
class Finding:
    """Something Tamga found that needs the owner."""

    # What it is about: Tamga writes about a record once.
    record: tuple[str, uuid.UUID]
    # Records whose chat already holds its context: the project a conversation may belong to.
    related: tuple[tuple[str, uuid.UUID], ...]
    # The name of a chat opened for it.
    title: str
    # What the run is told: code's words, with what was found fenced inside.
    prompt: str
    # The card under the message: its kind, and the id of the record it shows.
    card: dict[str, str]


async def speak_pending() -> int:
    """Writes to the owners about what waits for them and was not written about yet, within
    each company's budget and its day's chats. Returns how many messages were written."""
    if get_settings().tamga_requests_per_day == 0:
        return 0
    async with open_session() as db:
        company_ids = await linking.companies_awaiting_owner(db)
    written = 0
    for company_id in company_ids:
        async with open_session() as db:
            company = (await companies.get_companies(db, [company_id])).get(company_id)
            # Nobody is there to answer in a replay; its proposals are scored instead.
            if company is None or company.mode is CompanyMode.REPLAY:
                continue
            now = utcnow()
            # A chat opened for a turn whose worker then stopped holds nothing and never will.
            await repository.drop_abandoned(db, company_id, Origin.TAMGA, now - service.RUN_STALE)
            await db.commit()
            # Looked for even once the day's requests are spent: a finding past the day's
            # chats goes into the digest, which costs none.
            findings = await _findings(db, company_id)
        for finding in findings[:TURNS_A_PASS]:
            try:
                written += await _speak(company, finding)
            except Exception as error:  # one finding that fails never stops the others
                # The error's type only: a provider's error can quote what it was sent.
                logger.warning(
                    "writing about %s %s failed: %s", *finding.record, type(error).__name__
                )
    return written


async def _findings(db: AsyncSession, company_id: uuid.UUID) -> list[Finding]:
    """What waits for the owner and Tamga has not written about: the oldest first."""
    waiting: list[PlacementOut] = []
    # Every page of them: the list comes newest first, and the oldest is written about first.
    while True:
        page = await linking.list_placements(
            db,
            Actor.of_tamga(company_id),
            outcomes=[Outcome.PROPOSED, Outcome.ASKED],
            project_ids=[],
            page=PageParams(limit=PAGE, offset=len(waiting)),
        )
        waiting.extend(page.items)
        if not page.items or len(waiting) >= page.total:
            break
    waiting.sort(key=lambda item: (item.placed_at is None, item.placed_at))
    held = await projects.get_projects(
        db,
        company_id,
        [
            project_id
            for item in waiting
            for project_id in (item.project_id, *item.alternatives)
            if project_id is not None
        ],
    )
    told_projects = await repository.raised(db, company_id, agents.PROJECT, list(held))
    told_conversations = await repository.raised(
        db, company_id, agents.CONVERSATION, [item.thread_id for item in waiting]
    )
    findings: list[Finding] = []
    for item in waiting:
        subject = (item.conversation.subject if item.conversation else None) or "(no subject)"
        if item.outcome is Outcome.PROPOSED:
            project = held.get(item.project_id) if item.project_id else None
            # Answered on the Inbox before Tamga spoke: nothing is left to ask.
            if project is None or project.status is not ProjectStatus.PROPOSED:
                continue
            if project.id in told_projects:
                continue
            told_projects.add(project.id)
            findings.append(
                Finding(
                    record=(agents.PROJECT, project.id),
                    related=(),
                    title=f"{project.number} · {project.name}",
                    prompt=_told(
                        "a project Tamga proposes",
                        [
                            f"Project {project.number}: {project.name}",
                            f"Proposed from the conversation: {subject}",
                            *_reason(item),
                        ],
                        _PROPOSAL,
                    ),
                    card={"kind": CardKind.PROJECT_PROPOSAL.value, "id": str(project.id)},
                )
            )
        elif item.thread_id not in told_conversations:
            between = [held[project_id] for project_id in item.alternatives if project_id in held]
            findings.append(
                Finding(
                    record=(agents.CONVERSATION, item.thread_id),
                    related=tuple((agents.PROJECT, project.id) for project in between),
                    title=subject,
                    prompt=_told(
                        "a conversation Tamga could not choose a project for",
                        [
                            f"Conversation: {subject}",
                            "It could belong to: "
                            + "; ".join(
                                f"{project.number} ({project.name})" for project in between
                            ),
                            *_reason(item),
                        ],
                        _QUESTION,
                    ),
                    card={"kind": CardKind.CONVERSATION_QUESTION.value, "id": str(item.id)},
                )
            )
    return findings


async def _stands(db: AsyncSession, company_id: uuid.UUID, finding: Finding) -> bool:
    """Whether what was found still waits for the owner, who may have answered it on a page
    while the model wrote."""
    kind, record_id = finding.record
    if kind == agents.PROJECT:
        project = (await projects.get_projects(db, company_id, [record_id])).get(record_id)
        return project is not None and project.status is ProjectStatus.PROPOSED
    try:
        placement = await linking.get_placement(
            db, Actor.of_tamga(company_id), uuid.UUID(finding.card["id"])
        )
    except NotFound:
        return False
    return placement.outcome is Outcome.ASKED


def _reason(item: PlacementOut) -> list[str]:
    return [f"The linker's reason: {item.reason}"] if item.reason else []


def _told(label: str, lines: list[str], ask: str) -> str:
    return _WRITE_FIRST + llm.fenced_text(label, "\n".join(lines)) + ask


async def _speak(company: Company, finding: Finding) -> int:
    """Writes about one finding: admits the turn (where it goes, the chat free, the day's
    chats and budget), runs the assistant with no transaction open, and keeps what it wrote
    with the card, unless the finding was answered or the chat moved on meanwhile. Returns 1
    when a message or a card was left, 0 when the turn must wait or left nothing."""
    settings = get_settings()
    now = utcnow()
    run_id = uuid.uuid7()
    record_kind, record_id = finding.record
    async with open_session() as db:
        if await repository.raised(db, company.id, record_kind, [record_id]):
            return 0
        chat = await repository.holding(db, company.id, [finding.record, *finding.related])
        opened = chat is None
        if chat is None:
            since = now - service.RUN_WINDOW
            opened_today = await repository.opened_since(db, company.id, Origin.TAMGA, since)
            if opened_today >= settings.tamga_chats_per_day:
                return await _into_digest(db, company, finding)
            chat = Chat(company_id=company.id, origin=Origin.TAMGA, title=finding.title[:200])
            await repository.add(db, chat)
        elif service.is_running(chat, now):  # the owner is being answered there: next pass
            return 0
        elif context.due(chat):
            # The chat it goes to is nearly full: it is compacted first, from the budget of
            # Tamga's own turns, so the turn is not sent the whole of an outgrown chat.
            try:
                await service.compact(
                    db,
                    chat,
                    now,
                    began_by=Origin.TAMGA,
                    trace_content=companies.traces_content(company.kind),
                    whole=True,
                )
            except TooManyRequests:
                return 0
            except Exception as error:
                logger.warning("compacting a chat before a turn failed: %s", type(error).__name__)
            # The compaction wrote through sessions of its own: the row is read again, locked.
            now = utcnow()
            await db.refresh(chat, with_for_update=True)
            if service.is_running(chat, now):
                return 0
        await repository.lock_budget(db, company.id)
        spent = await repository.requests_since(
            db, company.id, Origin.TAMGA, now - service.RUN_WINDOW
        )
        if spent >= settings.tamga_requests_per_day:
            return 0
        await repository.add_run(
            db, company.id, run_id, began_by=Origin.TAMGA, requests=TURN_REQUESTS, at=now
        )
        history = await context.sent(db, company.id, chat.id)
        chat.running_since = now
        chat_id = chat.id
        await db.commit()

    seat = Seat(
        company_id=company.id, thread_id=None, actor=Actor.of_tamga(company.id), chat_id=chat_id
    )
    usage = RunUsage()
    written: list[ModelMessage] = []
    try:
        result = await llm.run(
            agents.assistant,
            finding.prompt,
            deps=seat,
            limits=UsageLimits(request_limit=TURN_REQUESTS),
            # Nobody wrote to it, so there is no language to answer in but the company's.
            language=company.language,
            trace_content=companies.traces_content(company.kind),
            usage=usage,
            history=history,
        )
        written = result.new_messages()
    except llm.NoAnswer:
        # Asking again would pay again for the same. The card alone tells the owner.
        logger.warning("no usable message about %s %s: the card stands alone", *finding.record)
    except BaseException:
        await service.end_run(company.id, chat_id, run_id, now, usage.requests)
        if opened:
            await _discard(company.id, chat_id)
        raise

    stored = False
    async with open_session() as db:
        kept = await repository.get_chat(db, company.id, chat_id, for_update=True)
        # Kept unless the chat was deleted while the model wrote, the turn was given up for
        # dead and the chat took another run, or the owner answered the finding meanwhile:
        # a card would then ask for a decision that is no longer open.
        if (
            kept is not None
            and kept.running_since == now
            and await _stands(db, company.id, finding)
        ):
            at = utcnow()
            if written:
                await repository.add_messages(
                    db,
                    kept,
                    context.dumped(written),
                    run_id=run_id,
                    began_by=Origin.TAMGA,
                    user_id=None,
                )
                answers = [message for message in written if isinstance(message, ModelResponse)]
                if answers:
                    last = answers[-1].usage
                    kept.context_tokens = last.input_tokens + last.output_tokens
            await repository.add_messages(
                db,
                kept,
                [finding.card],
                run_id=run_id,
                began_by=Origin.TAMGA,
                user_id=None,
                kind=MessageKind.CARD,
            )
            kept.last_message_at = at
            await repository.touch_record(db, kept, record_kind, record_id, at, raised=True)
            for opened_kind, opened_id in dict.fromkeys(seat.opened):
                await repository.touch_record(db, kept, opened_kind, opened_id, at)
            await events.publish(db, company.id, events.EventKind.CHAT, chat_id, by_tamga=True)
            await db.commit()
            stored = True
    await service.end_run(company.id, chat_id, run_id, now, usage.requests)
    if not stored and opened:
        await _discard(company.id, chat_id)
    return int(stored)


async def _into_digest(db: AsyncSession, company: Company, finding: Finding) -> int:
    """Puts a finding into the day's digest (ADR-0018, points 8 and 9): its card, with no model
    run. The digest then holds its record, so what is found about it later goes there. Returns
    1, 0 while the owner is being answered in the digest (the finding waits a pass) or once the
    owner answered it elsewhere. Commits."""
    now = utcnow()
    if not await _stands(db, company.id, finding):
        return 0
    day = clock.now(company).date()
    chat = await repository.digest_of(db, company.id, day)
    if chat is None:
        chat = Chat(company_id=company.id, origin=Origin.TAMGA, digest_on=day)
        await repository.add(db, chat)
    elif service.is_running(chat, now):
        return 0
    # A run given up for dead may still end: its answer, to the history before this card, is
    # not kept. And a digest the owner archived comes back with what is new in it.
    chat.running_since = None
    chat.archived_at = None
    await repository.add_messages(
        db,
        chat,
        [finding.card],
        run_id=uuid.uuid7(),
        began_by=Origin.TAMGA,
        user_id=None,
        kind=MessageKind.CARD,
    )
    chat.last_message_at = now
    await repository.touch_record(db, chat, *finding.record, now, raised=True)
    await events.publish(db, company.id, events.EventKind.CHAT, chat.id, by_tamga=True)
    await db.commit()
    return 1


async def _discard(company_id: uuid.UUID, chat_id: uuid.UUID) -> None:
    """A chat opened for a turn that then failed holds nothing: it goes, and the finding opens
    its chat again next time."""
    async with open_session() as db:
        chat = await repository.get_chat(db, company_id, chat_id, for_update=True)
        if chat is not None and chat.last_message_at is None:
            await repository.delete_chat(db, chat)
            await db.commit()
