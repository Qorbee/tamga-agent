"""Helper agents the chat calls as tools (ADR-0025, point 4): heavy reading done in a run of its
own, with a clean context, under its own request limit, so the chat keeps the answer and never the
mail or the pages behind it.

Each answers in claims that cite what they rest on: the record and words copied from it. Code
checks every quote against what the helper was given (ADR-0023); a claim left with no quote that
holds is dropped, and an answer left with no claim is no answer. A helper reads and nothing more:
``investigate`` has ``search`` and ``open`` only, ``read_file`` has no tools at all (ADR-0013).
"""

import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Final

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage

from app.config import get_settings
from app.modules.assistant import repository
from app.modules.assistant.looking import key, looking
from app.modules.assistant.models import Origin
from app.modules.assistant.seat import Seat
from app.modules.documents import service as documents
from app.modules.documents.models import ReadState
from app.platform import llm, search
from app.platform.clock import utcnow
from app.platform.db import open_session

logger = logging.getLogger(__name__)

INVESTIGATOR: Final = "investigator"
FILE_READER: Final = "file_reader"
# The most requests a helper's run may make: its searches and opens, and its answer.
INVESTIGATE_REQUESTS: Final = 12
READ_FILE_REQUESTS: Final = 2
# What the day's budget of the chat counts (``service.RUN_WINDOW``).
_BUDGET_WINDOW: Final = timedelta(days=1)
# What a helper is shown of one record, and of one file's pages, in characters.
_HELPER_OPENS: Final = 30_000
_FILE_READS: Final = 60_000
# A quote this short (folded) could be found almost anywhere ("Subject", "Trucks"), so it shows
# nothing.
_SHORTEST_QUOTE: Final = 15
# What a helper may say it could not find, besides its claims: uncited, so short.
_MISSING: Final = 300
# Helper runs one answer of the chat may start: each is a run of its own, paid apart.
HELPS_AN_ANSWER: Final = 3


class Citation(BaseModel):
    # The record as you opened it: its handle (c3, f4, i2, p1, s2) or a project's number.
    source: str
    # Words copied exactly from what `open` returned for it, that show the claim.
    quote: str = Field(max_length=400)


class Claim(BaseModel):
    # One plain sentence.
    says: str = Field(max_length=600)
    cited: list[Citation] = Field(min_length=1, max_length=4)


class Findings(BaseModel):
    claims: list[Claim] = Field(max_length=20)
    # What the question asks that the records you read did not show.
    missing: str = ""


class PageFact(BaseModel):
    # One plain sentence.
    says: str = Field(max_length=600)
    page: int = Field(ge=1)
    # Words copied exactly from that page that show it.
    quote: str = Field(max_length=400)


class FileFacts(BaseModel):
    facts: list[PageFact] = Field(max_length=20)
    # What the question asks that the file does not show.
    missing: str = ""


_INVESTIGATOR: Final = """\
You read a freight company's records to answer one question for Tamga, the company's \
assistant, which passes your answer on to the owner. The company arranges road transport: a \
client gives it a job, it carries the load or gives it on to a subcontractor; one project is \
one job.

You have two tools: search, over the company's mail, projects, invoices, files, firms, memory \
and past chats; and open, which gives one record by its handle. Search for what the question \
names, open what you find, and read until you can answer. Read every conversation the \
question needs, but nothing it does not. When you know you need several records, open them \
all in one step: each step costs the owner a wait.

Answer in claims. Each is one plain sentence, and each cites at least one record you opened: \
its handle as you opened it (c3, a project's number, i2, f4, p1, s2) and words copied exactly \
from what open returned for it, a few words to a sentence, that show the claim. Code checks \
every quote against what you opened and drops a claim no quote holds, so never cite what you \
did not open and never reword a quote. Every number, date, amount and plate a claim gives must \
be in its quotes, written as there. Put in `missing` what the question asks that the \
records did not show. Say only what the records show; names, numbers and dates stay as written.

"""

investigator: Agent[Seat, Findings] = Agent(
    name=INVESTIGATOR,
    deps_type=Seat,
    output_type=Findings,
    instructions=_INVESTIGATOR + llm.OUTSIDE_DATA,
    retries=2,
    model_settings=ModelSettings(timeout=180),
    toolsets=[looking],
)

_FILE_READER: Final = """\
You read one file of a freight company's records, given page by page, to answer one question \
for Tamga, the company's assistant. You have no tools: the file is all you have.

Answer in facts. Each is one plain sentence, the number of the page it is on, and words copied \
exactly from that page, a few words to a line, that show it. Code checks every quote against \
its page and drops a fact whose quote is not there, so never reword a quote. Every number, \
date, amount and plate a fact gives must be in its quote, written as there. Put in `missing` \
what the question asks that the file does not show. Say only what the file shows; names, \
numbers and dates stay as written.

"""

file_reader: Agent[None, FileFacts] = Agent(
    name=FILE_READER,
    output_type=FileFacts,
    instructions=_FILE_READER + llm.OUTSIDE_DATA,
    retries=1,
    model_settings=ModelSettings(timeout=180),
)

helping: FunctionToolset[Seat] = FunctionToolset()


@helping.tool
async def investigate(context: RunContext[Seat], question: str, project: str | None = None) -> str:
    """Hands a question that needs many records read, a job's many mails above all, to a helper
    that reads them in a run of its own and answers in claims, each citing the record and the
    words it rests on. Its reading never enters this chat, so it is the way to answer what would
    take more than a few opens. `project`, a project's number, is where it starts, when the
    question is about one job. Slower than a search: use it when search and open are not
    enough."""
    seat = context.deps
    helper = replace(seat, read={}, opened=[], open_cap=_HELPER_OPENS)
    prompt = question if project is None else f"{question}\n\nStart from project {project}."
    run_id, refusal = await _book(seat, INVESTIGATE_REQUESTS)
    if run_id is None:
        return refusal
    usage = RunUsage()
    try:
        result = await llm.run(
            investigator,
            prompt,
            deps=helper,
            limits=UsageLimits(request_limit=INVESTIGATE_REQUESTS),
            language=None,
            trace_content=seat.traces,
            usage=usage,
        )
    except llm.NoAnswer as error:
        return f"investigate gave no answer ({error})."
    except Exception as error:
        # The error's type only: a provider's error can quote what it was sent.
        logger.warning("investigate failed: %s", type(error).__name__)
        return "investigate could not run just now."
    finally:
        await _settle(seat, run_id, usage.requests)
        seat.opened.extend(helper.opened)
    kept, dropped = checked(result.output.claims, helper.read)
    return _said("what investigate found", kept, dropped, result.output.missing)


@helping.tool
async def read_file(context: RunContext[Seat], file: str, question: str) -> str:
    """Answers a question from what a file (f4, from a search or a project) says, by a helper
    with no tools that is given its pages: facts, each with its page and the words it rests on.
    Use it for what open does not show of a file: its lines, terms, amounts or names."""
    seat = context.deps
    number = file.strip().removeprefix("f")
    document_id = seat.look("f", int(number)) if number.isdigit() else None
    if document_id is None:
        return f"No such file: {file} was not given by a tool in this chat."
    async with open_session() as db:
        document = (await documents.get_documents(db, seat.company_id, [document_id])).get(
            document_id
        )
    if document is None:
        return "That file is gone."
    if document.read_state is not ReadState.READ or not document.text.strip():
        return "Tamga has not read that file's text yet: there is nothing to answer from."
    pages = document.text.split("\f")
    shown: list[str] = []
    used = 0
    for page in pages:
        if used + len(page) > _FILE_READS:
            if not shown:
                # One page longer than all it may be shown: its start.
                shown.append(page[:_FILE_READS])
            break
        shown.append(page)
        used += len(page)
    marked = "\n\n".join(f"[page {n}]\n{page}" for n, page in enumerate(shown, start=1))
    if len(shown) < len(pages):
        marked += f"\n\n(pages {len(shown) + 1} to {len(pages)} are not shown)"
    run_id, refusal = await _book(seat, READ_FILE_REQUESTS)
    if run_id is None:
        return refusal
    usage = RunUsage()
    try:
        result = await llm.run(
            file_reader,
            [
                f"The question: {question}\n\n",
                *llm.fenced(f"the file {document.filename}", [marked]),
            ],
            deps=None,
            limits=UsageLimits(request_limit=READ_FILE_REQUESTS),
            language=None,
            trace_content=seat.traces,
            usage=usage,
        )
    except llm.NoAnswer as error:
        return f"read_file gave no answer ({error})."
    except Exception as error:
        logger.warning("read_file failed: %s", type(error).__name__)
        return "read_file could not run just now."
    finally:
        await _settle(seat, run_id, usage.requests)
    by_page = {str(n): page for n, page in enumerate(shown, start=1)}
    claims = [
        Claim(says=fact.says, cited=[Citation(source=str(fact.page), quote=fact.quote)])
        for fact in result.output.facts
    ]
    kept, dropped = checked(claims, by_page)
    return _said(
        f"what read_file found in {file}",
        kept,
        dropped,
        result.output.missing,
        cites="page ",
    )


def checked(claims: Sequence[Claim], read: Mapping[str, str]) -> tuple[list[Claim], int]:
    """The claims with the citations whose quote is in the record they name, as the helper was
    given it, and how many claims were dropped (ADR-0023): one left with no such citation, or
    giving a number, a date or a plate its quotes do not hold."""
    folded = {key(source): search.fold(text) for source, text in read.items()}
    kept = []
    for claim in claims:
        holding = [
            citation
            for citation in claim.cited
            if len(quote := search.fold(citation.quote)) >= _SHORTEST_QUOTE
            and quote in folded.get(key(citation.source), "")
        ]
        quoted = "".join("".join(search.fold(citation.quote).split()) for citation in holding)
        if holding and all(number in quoted for number in _numbers(claim.says)):
            kept.append(claim.model_copy(update={"cited": holding}))
    return kept, len(claims) - len(kept)


def _numbers(text: str) -> list[str]:
    """The words of a text that hold a digit (an amount, a date, a plate, an invoice number),
    folded, with their spaces gone as a search ignores them."""
    return [word for word in search.fold(text).split() if _DIGIT.search(word)]


_DIGIT: Final = re.compile(r"\d")


def _said(label: str, kept: Sequence[Claim], dropped: int, missing: str, cites: str = "") -> str:
    lines = [
        f"- {claim.says} ["
        + "; ".join(f'{cites}{citation.source}: "{citation.quote}"' for citation in claim.cited)
        + "]"
        for claim in kept
    ]
    if dropped:
        lines.append(f"({dropped} more left out: what they cited is not in what it read)")
    if not kept:
        # With no claim that holds there is no answer: nothing uncited reaches the chat either.
        lines.insert(0, "Nothing it read answers the question.")
    elif missing.strip():
        lines.append(f"Not shown by what it read: {' '.join(missing.split())[:_MISSING]}")
    return llm.fenced_text(label, "\n".join(lines))


async def _book(seat: Seat, requests: int) -> tuple[uuid.UUID | None, str]:
    """Reserves a helper's requests from the day's budget of the chat, as a run's own are, or
    says why not: the budget is spent, or this answer started its helpers already."""
    if seat.helped >= HELPS_AN_ANSWER:
        return None, "No more helpers for this answer: answer from what you have."
    now = utcnow()
    async with open_session() as db:
        await repository.lock_budget(db, seat.company_id)
        spent = await repository.requests_since(
            db, seat.company_id, Origin.OWNER, now - _BUDGET_WINDOW
        )
        if spent + requests > get_settings().chat_requests_per_day:
            return None, "The chat's requests for today are spent: answer from what you have."
        run_id = uuid.uuid7()
        await repository.add_run(
            db, seat.company_id, run_id, began_by=Origin.OWNER, requests=requests, at=now
        )
        await db.commit()
    seat.helped += 1
    return run_id, ""


async def _settle(seat: Seat, run_id: uuid.UUID, requests: int) -> None:
    async with open_session() as db:
        await repository.settle_run(db, seat.company_id, run_id, requests)
        await db.commit()
