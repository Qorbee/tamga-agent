"""The turns Tamga takes on its own (ADR-0018): when its background work finds something that
needs the owner, it writes first, in the chat that already holds the finding's record or in a
new one, and code puts a card under what it wrote.

What makes it speak is a list in code, never a prompt's choice: a project the linker proposes,
a conversation it cannot choose a project for, and the bills that wait for the owner's approval,
written about together, in one message with one card, so one pass of the payment path reads as
one subject.
Each is read from where it stands (the placements and decisions that wait for the owner), so
nothing is lost when a run fails or the worker stops: what was not written about yet is found
again.

Past the day's new chats, a finding goes into the day's digest instead (``_into_digest``): its
card alone, written by code with no model run, so a busy day costs no more and nothing waits for
the next day. A bill that waits for approval is never held back by the cap (ADR-0018, point 9):
it opens its chat whatever the day's count.

Such a turn acts for the company, not for a person. It runs as ``Actor.of_tamga``, which every
role check refuses, on the assistant's tools, which only read. What it is told of the finding
is outside text and reaches it fenced. It is one job that never overlaps with itself
(``tasks.speak``), so two findings about one record cannot open two chats.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import batched
from typing import Any, Final

from pydantic_ai import UsageLimits
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.assistant import agents, context, repository, service
from app.modules.assistant.models import CardKind, Chat, MessageKind, Origin
from app.modules.assistant.seat import Seat
from app.modules.books import service as books
from app.modules.books.models import InvoiceDirection
from app.modules.books.schemas import InvoiceOut
from app.modules.companies import service as companies
from app.modules.companies.models import Company, CompanyMode
from app.modules.decisions import service as decisions
from app.modules.decisions.schemas import DecisionOut
from app.modules.linking import service as linking
from app.modules.linking.models import Outcome
from app.modules.linking.schemas import PlacementOut
from app.modules.money import service as money
from app.modules.playground import service as playground
from app.modules.policy import service as policy
from app.modules.projects import service as projects
from app.modules.projects.models import Project, ProjectStatus
from app.platform import chain, clock, events, llm
from app.platform.auth import Actor
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import NotFound, TooManyRequests
from app.platform.language import Language
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
_OPENING: Final = (
    "\nSomeone just opened this company to try Tamga. Open the project and look at its trucks, "
    "papers and bills. Then write a short welcome, under five sentences: what the company faces "
    "today and what you would look at first. Do not explain the app; a card under your message "
    "shows the job."
)
_APPROVALS: Final = (
    "\nOpen the projects these bills belong to, each once, and look at their trucks and papers. "
    "Then write the owner one short message about the bills together, four sentences at most: "
    "how many wait and what they come to, which you would pay now and which you would hold, "
    "each with one reason. A rule that could not be checked was not checked: never say it was "
    "breached or broken. A card under your message lists every bill with what Tamga would do and "
    "lets them approve or hold, so do not go through the bills one by one or say how. Nothing is "
    "paid until they approve."
)
# A bill is written about once the controller weighed it, or once this has passed since its
# evaluation: an opinion that does not come never holds the owner's approval back.
ADVICE_WAIT: Final = timedelta(minutes=15)
# The most bills one message and its card hold: what one approval of the card can answer.
BATCH: Final = 50
# From this hour of a company's business day (UTC, nine in Turkey), Tamga writes its brief.
BRIEF_HOUR: Final = 6
# The brief's chat is named in the company's language, as Tamga writes there.
BRIEF_TITLES: Final = {Language.EN: "Morning brief", Language.TR: "Sabah özeti"}
_BRIEF: Final = (
    "\nIt is the start of the owner's day. Write a short good morning, three sentences at most: "
    "what matters today in these numbers and what you would look at first. Open a project or a "
    "bill only if one needs them today. The numbers are the code's; never work out others. A "
    "card under your message shows where the money stands, so do not list every figure."
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
    # More records the same message is about: the other bills of a batch.
    also: tuple[tuple[str, uuid.UUID], ...]
    # Records whose chat already holds its context: the project a conversation may belong to.
    related: tuple[tuple[str, uuid.UUID], ...]
    # The name of a chat opened for it.
    title: str
    # What the run is told: code's words, with what was found fenced inside.
    prompt: str
    # The card under the message: its kind, the id of the record it shows, and for a batch
    # every record's id and evaluation.
    card: dict[str, Any]


async def speak_pending() -> int:
    """Writes to the owners about what waits for them and was not written about yet, within
    each company's budget and its day's chats. Returns how many messages were written."""
    if get_settings().tamga_requests_per_day == 0:
        return 0
    async with open_session() as db:
        company_ids = sorted(
            set(await linking.companies_awaiting_owner(db))
            | set(await decisions.companies_awaiting_owner(db))
            | set(await playground.just_opened(db))
            # Each is due its morning brief once a day.
            | set(await companies.awake_companies(db, utcnow()))
        )
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
    """What waits for the owner and Tamga has not written about: bills to approve first, then
    the oldest placements first. A sandbox that just opened is welcomed before anything."""
    findings = [*await _opening(db, company_id), *await _approvals(db, company_id)]
    company = (await companies.get_companies(db, [company_id]))[company_id]
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
                    also=(),
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
                    also=(),
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
    # Last: what waits for an answer comes before the day's numbers.
    return [*findings, *await _morning(db, company)]


def brief_id(company_id: uuid.UUID, day: date) -> uuid.UUID:
    """A brief's record: one a company a day."""
    return uuid.uuid5(company_id, day.isoformat())


async def _morning(db: AsyncSession, company: Company) -> list[Finding]:
    """The day's brief, once its business morning came: the money's numbers, worked out by
    code, for a few words of the model's. Only when there is something to say: something waits
    for the owner or falls due, and the picture is not one a brief already gave. Otherwise no
    run, so a quiet day costs nothing. Not on the day a company opens, when Tamga already met
    the owner, and in the chat of the week's briefs while there is one."""
    now = clock.now(company)
    today = now.date()
    if now.hour < BRIEF_HOUR or today <= company.created_at.date():
        return []
    record_id = brief_id(company.id, today)
    if await repository.raised(db, company.id, agents.BRIEF, [record_id]):
        return []
    figures = await _figures(db, company, today)
    if not figures.news:
        return []
    # The picture itself is a record: a brief raises it, so the same numbers are never briefed
    # twice, however many days apart.
    picture = uuid.uuid5(company.id, "\n".join(figures.values))
    if await repository.raised(db, company.id, agents.BRIEF_PICTURE, [picture]):
        return []
    return [
        Finding(
            record=(agents.BRIEF, record_id),
            also=((agents.BRIEF_PICTURE, picture),),
            # The week's chat: the earlier briefs of the same week, Monday on, so a chat holds
            # one week and the next Monday opens another.
            related=tuple(
                (agents.BRIEF, brief_id(company.id, today - timedelta(days=back)))
                for back in range(1, today.weekday() + 1)
            ),
            title=BRIEF_TITLES[company.language],
            prompt=_told("the company's money this morning", figures.lines, _BRIEF),
            card={"kind": CardKind.MORNING_BRIEF.value, "id": str(record_id)},
        )
    ]


@dataclass(frozen=True)
class Figures:
    """A brief's numbers: its lines for the model, what asks something of the owner without the
    day's dates (to tell a new picture from one already given), and whether any does."""

    lines: list[str]
    values: list[str]
    news: bool


async def _figures(db: AsyncSession, company: Company, today: date) -> Figures:
    """The brief's numbers, in the policy's currency where they are one sum; amounts in other
    currencies are said apart, never added up."""
    actor = Actor.of_tamga(company.id)
    currency = (await policy.current_policy(db, company.id)).currency
    held = await money.holdings_of(db, company.id, currency)
    waiting = len(await decisions.awaiting_owner(db, company.id))

    async def owed(direction: InvoiceDirection, before: date) -> str:
        totals = await books.invoice_totals(
            db, actor, books.InvoiceFilter(direction=direction, outstanding=True, due_before=before)
        )
        return "; ".join(f"{t.remaining_amount} {t.currency} ({t.count})" for t in totals) or "none"

    week = today + timedelta(days=8)
    cash = (f"{held.total} {currency}" if held.items else "no balance known") + (
        "" if held.complete else " (an account with no rate is left out)"
    )
    to_pay = await owed(InvoiceDirection.PAYABLE, week)
    to_come = await owed(InvoiceDirection.RECEIVABLE, week)
    overdue = await owed(InvoiceDirection.RECEIVABLE, today)
    return Figures(
        lines=[
            f"Today: {today.isoformat()}",
            f"In the bank and cash accounts: {cash}",
            f"Bills that wait for the owner's approval: {waiting}",
            f"Bills to pay due by {week.isoformat()}: {to_pay}",
            f"Clients' invoices due by then: {to_come}",
            f"Clients' invoices overdue: {overdue}",
        ],
        # Cash moves every day; what asks something of the owner is the news.
        values=[str(waiting), to_pay, to_come, overdue],
        # Cash alone asks nothing of the owner.
        news=bool(waiting) or any(value != "none" for value in (to_pay, to_come, overdue)),
    )


async def _opening(db: AsyncSession, company_id: uuid.UUID) -> list[Finding]:
    """A sandbox's first job, written about once when it opens, so whoever opened it is met by
    Tamga before any bill waits for them."""
    scenario = await playground.scenario_of(db, company_id)
    if scenario is None:
        return []
    first = await projects.list_projects(
        db,
        Actor.of_tamga(company_id),
        statuses=[],
        q=None,
        suggested=None,
        sort="starts_on",
        page=PageParams(limit=1, offset=0),
    )
    if not first.items:
        return []
    project = first.items[0]
    if await repository.raised(db, company_id, agents.OPENING, [project.id]):
        return []
    return [
        Finding(
            record=(agents.OPENING, project.id),
            also=(),
            related=((agents.PROJECT, project.id),),
            title=scenario.title,
            prompt=_told(
                "the company that just opened",
                [
                    f"Scenario: {scenario.title}. {scenario.summary}",
                    f"Its first job: {project.number} ({project.name})",
                ],
                _OPENING,
            ),
            card={"kind": CardKind.PROJECT_PROPOSAL.value, "id": str(project.id)},
        )
    ]


async def _approvals(db: AsyncSession, company_id: uuid.UUID) -> list[Finding]:
    """The bills that wait for the owner's approval and Tamga has not written about, together:
    one message and one card for up to ``BATCH`` of them. A bill the controller is still
    weighing joins the others while they are recent; once the oldest ready one has waited
    ``ADVICE_WAIT``, the ready ones are written about without it, so a steady stream of new bills
    never keeps the old ones from the owner."""
    waiting = await decisions.awaiting_owner(db, company_id)
    told = await repository.raised(db, company_id, agents.DECISION, [item.id for item in waiting])
    waiting = [item for item in waiting if item.id not in told]
    bills = {item.id: item.invoice for item in waiting if item.invoice is not None}
    waiting = [item for item in waiting if item.id in bills]
    # The controller weighs each evaluation within minutes, while the company's budget lasts.
    weighing = await decisions.advice_left(db, company_id) > 0
    now = utcnow()
    ready = [
        item
        for item in waiting
        if item.advice is not None or not weighing or now - item.evaluated_at >= ADVICE_WAIT
    ]
    if not ready:
        return []
    if len(ready) < len(waiting) and now - min(item.evaluated_at for item in ready) < ADVICE_WAIT:
        return []
    jobs = await decisions.jobs_of(db, company_id, [item.invoice_id for item in ready])
    known = await projects.get_projects(
        db, company_id, sorted({job for held in jobs.values() for job in held})
    )
    return [_batch(chunk, bills, jobs, known) for chunk in batched(ready, BATCH, strict=False)]


def _batch(
    waiting: Sequence[DecisionOut],
    bills: dict[uuid.UUID, InvoiceOut],
    jobs: dict[uuid.UUID, list[uuid.UUID]],
    known: dict[uuid.UUID, Project],
) -> Finding:
    lines: list[str] = []
    related: dict[uuid.UUID, None] = {}
    for index, item in enumerate(waiting, 1):
        bill = bills[item.id]
        held = {job: known[job] for job in jobs.get(item.invoice_id, []) if job in known}
        related.update(dict.fromkeys(held))
        lines += [
            f"Bill {index}: {bill.number or '(no number)'} from {bill.party_name or '(unknown)'}",
            f"Amount: {item.amount} {item.currency or ''}, due {bill.due_date or '?'}",
            *_mirrored(item),
            *_advised(item),
            "Its jobs: "
            + ("; ".join(f"{p.number} ({p.name})" for p in held.values()) or "none confirmed"),
            *(
                f"Rule {rule.rule}: {rule.outcome.value}, {rule.reason}"
                for rule in item.rules
                if rule.reason
            ),
            "",
        ]
    first = waiting[0]
    bill = bills[first.id]
    title = (
        f"{bill.party_name or 'A subcontractor'} · {bill.number or 'a bill'}"
        if len(waiting) == 1
        else f"{len(waiting)} bills to approve"
    )
    return Finding(
        record=(agents.DECISION, first.id),
        also=tuple((agents.DECISION, item.id) for item in waiting[1:]),
        related=tuple((agents.PROJECT, project_id) for project_id in related),
        title=title,
        prompt=_told("the bills that wait for the owner's approval", lines[:-1], _APPROVALS),
        # Every bill stays on the card, as the message speaks of all of them: one answered
        # meanwhile shows as answered there.
        card={
            "kind": CardKind.DECISION_BATCH.value,
            "id": str(first.id),
            "ids": [str(item.id) for item in waiting],
            "evaluations": [item.evaluation for item in waiting],
        },
    )


def _advised(item: DecisionOut) -> list[str]:
    advice = item.advice
    if advice is None:
        return []
    part = f" ({advice.amount} {item.currency or ''} now)" if advice.amount is not None else ""
    return [f"The controller proposes: {advice.proposal}{part}. Its reason: {advice.reason}"]


def _mirrored(item: DecisionOut) -> list[str]:
    if item.usdc_units is None:
        return []
    return [f"It would be paid as {chain.usdc(item.usdc_units)} test USDC from the vault"]


async def _stands(db: AsyncSession, company_id: uuid.UUID, finding: Finding) -> bool:
    """Whether what was found still waits for the owner, who may have answered it on a page
    while the model wrote."""
    kind, record_id = finding.record
    if kind in (agents.OPENING, agents.BRIEF):
        return True
    if kind == agents.DECISION:
        ids = [record_id for _, record_id in (finding.record, *finding.also)]
        return bool(await decisions.still_waiting(db, company_id, ids))
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
        chat = await repository.holding(
            db, company.id, [finding.record, *finding.also, *finding.related]
        )
        opened = chat is None
        if chat is None:
            since = now - service.RUN_WINDOW
            opened_today = await repository.opened_since(db, company.id, Origin.TAMGA, since)
            # A bill to approve is never held back by the cap (ADR-0018, point 9).
            capped = finding.record[0] != agents.DECISION
            if capped and opened_today >= settings.tamga_chats_per_day:
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
            db,
            company.id,
            run_id,
            began_by=Origin.TAMGA,
            requests=TURN_REQUESTS,
            at=now,
        )
        history = await context.sent(db, company.id, chat.id)
        # The brief's part that changes, after the history and never kept (ADR-0025).
        now_is = ModelRequest(
            parts=[UserPromptPart(content=await agents.situation(db, Actor.of_tamga(company.id)))]
        )
        chat.running_since = now
        chat_id = chat.id
        # Said at once, not only when the message lands: the owner sees Tamga at work in it.
        await events.publish(db, company.id, events.EventKind.CHAT, chat_id)
        await db.commit()

    seat = Seat(
        company_id=company.id,
        thread_id=None,
        actor=Actor.of_tamga(company.id),
        chat_id=chat_id,
        traces=companies.traces_content(company.kind),
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
            history=[*history, now_is],
            settings_name=agents.SPEAKER,
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
            await repository.give_reason(db, company.id, run_id, finding.title[:200])
            for kind, one in (finding.record, *finding.also):
                await repository.touch_record(db, kept, kind, one, at, raised=True)
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
    for kind, one in (finding.record, *finding.also):
        await repository.touch_record(db, chat, kind, one, now, raised=True)
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
