"""What Tamga learned this week (ADR-0032). Once a week, when the week left something to learn
from, the learner drafts what the company's memory should hold: from why the owner took items
out of projects, who writes from the company's own mailboxes, and what the owner's chats were
about. Tamga then opens a chat, "What I learned", whose one card lists each draft to keep or
skip, and the entries the week shows wrong to forget. Nothing holds until the owner keeps it.
"""

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from pydantic_ai import UsageLimits
from pydantic_ai.usage import RunUsage

from app.modules.assistant import agents, repository, turns
from app.modules.assistant.models import CardKind
from app.modules.companies import service as companies
from app.modules.companies.models import Company, CompanyMode
from app.modules.conversations import service as conversations
from app.modules.memory import service as memory
from app.modules.memory.models import MemorySource, MemoryState
from app.modules.memory.schemas import MemoryEntryOut
from app.modules.projects import service as projects
from app.platform import clock, llm
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.errors import DomainError
from app.platform.language import Language

logger = logging.getLogger(__name__)

WEEK: Final = timedelta(days=7)
TITLES: Final = {Language.EN: "What I learned", Language.TR: "Bu hafta öğrendiklerim"}
# How much of the week the learner reads: enough to see what repeats, little enough to be cheap.
_SENDERS: Final = 15
_CHATS: Final = 10
_TELL: Final = (
    "\nThe learner drafted these lessons from the week, for the owner to keep or skip. Write the "
    "owner two short sentences: what you learned this week and from what. The card with your "
    "message lists each lesson with Keep and Skip, so do not list them or say how."
)


def week_of(company_id: uuid.UUID, day: date) -> tuple[uuid.UUID, datetime]:
    """A week's record, one a company a week, and when that week began (Monday, UTC)."""
    year, week, weekday = day.isocalendar()
    began = datetime.combine(day - timedelta(days=weekday - 1), time(), tzinfo=UTC)
    return uuid.uuid5(company_id, f"learned {year}-W{week:02d}"), began


async def learn_pending() -> int:
    """Each company's week, once, for those awake. Returns how many cards were written."""
    async with open_session() as db:
        company_ids = await companies.awake_companies(db, utcnow())
    written = 0
    for company_id in company_ids:
        try:
            written += await learn(company_id)
        except Exception as error:  # one company that fails never stops the others
            # The error's type only: a provider's error can quote what it was sent.
            logger.warning("learning for %s failed: %s", company_id, type(error).__name__)
    return written


async def learn(company_id: uuid.UUID) -> int:
    """The company's week: drafted once, from a week that left something, and written about
    once. Returns 1 when its card was written."""
    async with open_session() as db:
        company = (await companies.get_companies(db, [company_id])).get(company_id)
        if company is None or company.mode is CompanyMode.REPLAY:
            return 0
        # The wall clock's week, a sandbox's too: what the learner drafted and read is stamped so.
        record_id, began = week_of(company.id, utcnow().date())
        if await repository.raised(db, company.id, agents.LEARNED, [record_id]):
            return 0
        # Drafted already, and its card not written yet (the day's chats were spent): written now.
        drafted = await memory.learned_since(db, company.id, began)
        ran = await conversations.model_reads_since(db, company.id, began, agents.LEARNER)
    why = ""
    if not drafted:
        if ran:
            return 0
        drafted, why = await _draft(company)
        if not drafted:
            return 0
    finding = turns.Finding(
        record=(agents.LEARNED, record_id),
        also=(),
        related=(),
        title=TITLES[company.language],
        prompt=turns.told(
            "what the learner drafted this week",
            [f"- {entry.kind.value}: {entry.text}" for entry in drafted]
            + ([f"The learner's account of where they came from: {why}"] if why else []),
            _TELL,
        ),
        card={
            "kind": CardKind.LEARNED.value,
            "id": str(record_id),
            "ids": [str(entry.id) for entry in drafted],
        },
    )
    return await turns.speak(company, finding)


async def _draft(company: Company) -> tuple[list[MemoryEntryOut], str]:
    """One request of the learner over the week, its drafts written as the memory's proposals,
    with its account of what they came from. Nothing when the week left nothing to learn from."""
    since = utcnow() - WEEK
    async with open_session() as db:
        # An answer is dated in the company's business time; mail and chats by the wall clock.
        refused = await projects.reasons_since(db, company.id, clock.now(company) - WEEK)
        senders = await conversations.own_senders(db, company.id, since, _SENDERS)
        chats = await repository.summaries_since(db, company.id, since, _CHATS)
        entries = await memory.for_memorist(db, company.id)
        left = await conversations.model_reads_left(db, company.id)
    if not (refused or senders or chats) or left <= 0:
        return [], ""
    handles = {f"m{at}": entry.id for at, entry in enumerate(entries, start=1)}
    week = [
        *(
            f"Taken out of {each.project} ({each.item_kind.value}): {each.because}"
            + (f", the owner said: {each.note}" if each.note else "")
            for each in refused
        ),
        *(
            f"Writes from the company's mailbox: {name} <{address}>, {mails} mails"
            for name, address, mails in senders
        ),
        *(f"A chat of the owner's, {chat.title}: {chat.summary}" for chat in chats),
    ]
    usage = RunUsage()
    try:
        run = await llm.run(
            agents.learner,
            agents.memorist_case(entries, handles, "\n".join(week), None),
            deps=agents.MemorySeat(handles=handles, may_change=True),
            # A retry after an answer that failed its checks, only where the day's budget has it.
            limits=UsageLimits(request_limit=min(left, 2)),
            language=company.language,
            trace_content=companies.traces_content(company.kind),
            usage=usage,
        )
    finally:
        async with open_session() as db:
            await conversations.count_model_reads(
                db, company.id, agents.LEARNER, requests=max(usage.requests, 1)
            )
            await db.commit()
    by_handle = {handle: entry for handle, entry in zip(handles, entries, strict=True)}
    drafted: list[MemoryEntryOut] = []
    async with open_session() as db:
        for edit in run.output.changes:
            held = by_handle.get(edit.entry or "")
            try:
                if edit.action == "add" and edit.kind and edit.text:
                    drafted.append(
                        await memory.draft(
                            db,
                            company.id,
                            kind=edit.kind,
                            text=edit.text,
                            party_id=None,
                            project_id=None,
                            chat_id=None,
                            written_by=None,
                            title=edit.name,
                            source=MemorySource.LEARNER,
                        )
                    )
                elif edit.action == "change" and held and held.state is MemoryState.HELD:
                    drafted.append(
                        await memory.draft(
                            db,
                            company.id,
                            kind=edit.kind or held.kind,
                            text=edit.text or held.text,
                            party_id=held.party_id,
                            project_id=held.project_id,
                            chat_id=None,
                            written_by=None,
                            title=edit.name or held.title,
                            replaces_id=held.id,
                            source=MemorySource.LEARNER,
                        )
                    )
            except DomainError as error:
                logger.warning("a lesson was not drafted: %s", type(error).__name__)
    return drafted, run.output.reason
