"""The linking module's agent (ADR-0005, ADR-0010): ``linker`` reads one conversation as a person
would, looks through the company's own mail, projects and invoices with tools that only read,
and says where the conversation belongs.

Everything it is shown comes from outside (mail text, file names, a project's name taken from
mail), so the user turn and every tool result are fenced as data. Its answer is a proposal: code
checks it against what the run was shown and writes suggested links, which the owner confirms.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import UserContent
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.books import service as books
from app.modules.books.schemas import InvoiceOut
from app.modules.conversations import service as conversations
from app.modules.conversations.service import ThreadRead
from app.modules.documents import service as documents
from app.modules.facts import extractors
from app.modules.facts import service as facts
from app.modules.projects import service as projects
from app.modules.projects.models import LinkStatus, ProjectStatus
from app.platform import llm
from app.platform.db import open_session
from app.platform.items import ItemKind

LINKER: Final = "linker"
# What a placement's ``placed_by`` holds when this agent placed it.
MODEL_PLACED: Final = f"model:{LINKER}"
# The kinds of record a run opens.
PROJECT: Final = "project"
CONVERSATION: Final = "conversation"

# What the model is shown of one conversation: its first mails and its last, and this much of
# each one's text. A long reply chain repeats itself; its start and its end say what it is.
_FIRST_MAILS: Final = 3
_LAST_MAILS: Final = 9
_MAIL_TEXT: Final = 2000
# Results a search returns: enough to choose from, few enough to read.
_FOUND: Final = 8
# Plates are not keys (a truck runs many jobs), but they are what a person searches by.
_SHOWN_KINDS: Final = frozenset({"plate"})


@dataclass
class Desk:
    """What a run works for, and what it was shown. The model can name a conversation or a
    project only by a handle or a number it was given in this run, so nothing it answers can
    point outside the company or at something it never saw."""

    company_id: uuid.UUID
    # The conversation being placed, which a search leaves out; None for a run that places none
    # (the assistant's).
    thread_id: uuid.UUID | None
    # Handle → thread id, in the order conversations were shown.
    conversations: dict[int, uuid.UUID] = field(default_factory=dict)
    # A project's number, without spaces and in upper case → its id.
    projects: dict[str, uuid.UUID] = field(default_factory=dict)
    # The numbers of the projects the owner said this conversation does not belong to.
    refused: set[str] = field(default_factory=set)
    # The records the run opened by a handle or a number, in order: (kind, id). One that only
    # passed by in a search is not among them.
    opened: list[tuple[str, uuid.UUID]] = field(default_factory=list)

    def handle(self, thread_id: uuid.UUID) -> int:
        for handle, shown in self.conversations.items():
            if shown == thread_id:
                return handle
        handle = len(self.conversations) + 1
        self.conversations[handle] = thread_id
        return handle

    def show(self, number: str, project_id: uuid.UUID) -> None:
        self.projects[bare(number)] = project_id


def bare(number: str) -> str:
    return "".join(number.split()).upper()


class Job(BaseModel):
    """What the mail says of the job. A field the mail does not show is left empty."""

    # The firm that gives the company the job and is invoiced for it.
    client: str | None = None
    # The carrier the company gave the job on to.
    subcontractor: str | None = None
    # The firm that receives the goods: often not the client.
    consignee: str | None = None
    # Where it is loaded and where it is delivered: the town and the country, no street address.
    origin: str | None = None
    destination: str | None = None
    # The load, in a few words.
    cargo: str | None = None
    trucks: int | None = Field(default=None, ge=1, le=500)
    # The first day of loading and the last day of delivery, as the mail states them.
    starts_on: date | None = None
    ends_on: date | None = None


class NewProject(Job):
    # A few words on the job. Code names the project by its client, route and first day when
    # the mail shows them; this is the name only when it shows neither client nor route.
    name: str


class Placing(BaseModel):
    decision: Literal["link", "new_project", "ask", "unplaced"]
    # link: the number of the project it belongs to.
    project: str | None = None
    # ask: the numbers of the projects that fit equally.
    alternatives: list[str] = []
    new_project: NewProject | None = None
    # link: what this conversation adds to what is known of the job.
    job: Job | None = None
    # link, new_project: the other side of this conversation, and what it is to the company in
    # this job.
    party: str | None = None
    party_role: Literal["client", "subcontractor", "other"] | None = None
    # One or two plain sentences for the owner.
    reason: str
    # The mails of this conversation the decision rests on, by their number in it.
    cited_mails: list[int] = []
    confidence: float = Field(ge=0, le=1)


_INSTRUCTIONS: Final = """\
You place one mail conversation of a freight company among its projects. A project is one job: \
one client's load, or loads moved together, from the order to the last payment. You are shown \
the conversation, and your tools search the company's own mail, projects and invoices. Nothing \
you do changes anything: your answer is a proposal that the owner confirms.

How to work:
- Read the conversation: who writes, what is moved, from where to where and when, and every \
number in it (order, booking, CMR, invoice, plate).
- Look for the job. Search projects and mail by those numbers and plates first, then by the \
other party's name and the dates. Two mails about one job need not be replies to each other and \
often do not repeat its number, so look at what the same party wrote around those days. Open a \
conversation or a project before you rely on it.
- The same party with two jobs is two projects. A party alone never ties a conversation to a \
project: something of the job itself must match, such as a number, a plate with dates that fit, \
or the route together with the dates and the cargo.

Answer with one decision:
- link: it belongs to one project you were shown. Give that project's number.
- new_project: it shows a job that no project holds: an order placed or confirmed, a truck or a \
load being arranged, a loading or a delivery under way, documents or an invoice for a job. Fill \
`new_project` with what the mail says of the job.
- ask: two or more projects fit and nothing in the mail tells them apart. Give their numbers.
- unplaced: it is not about one job (a price list, an introduction, a general question, a quote \
asked for and not agreed), or you cannot tell. Between a guess and unplaced, answer unplaced: a \
wrong link is worse than a missing one.

For link and new_project, say who the company deals with in this conversation (`party`, the \
other firm's name as the mail writes it) and what that firm is to the company in this job \
(`party_role`):
- client: it gives the company the job. It orders or confirms the transport, asks where the \
truck is, and is the one the company invoices.
- subcontractor: the company gives it the job. It offers or supplies the truck and the driver, \
reports to the company, and invoices the company.
- other: anyone else, such as a customs agent, a warehouse or the consignee.
A freight company can be either: go by who orders and who carries in this conversation, never \
by the firm's name. Leave both empty when the mail does not show it.

What is known of a job is kept as fields, in `new_project` for a new one and in `job` for a \
link (there, only what this conversation adds). Fill a field only with what the mail states; \
leave it empty otherwise, never a guess:
- client: the firm that gives the company the job and is invoiced for it.
- subcontractor: the carrier the company gave the job on to.
- consignee: the firm that receives the goods, which is often not the client.
- origin, destination: where it is loaded and where it is delivered, as the town and the \
country ("Bukovik, Serbia"), never a street address.
- cargo: the load in a few words. trucks: how many trucks the job is.
- starts_on: the first day of loading. ends_on: the last day of delivery. A day the mail gives \
without a year is in the year of that mail.
- name (a new project only): a few words on the job. The project is named by its client, \
route and first day when you fill them, so this is used only when the mail shows neither.

`reason` is one or two plain sentences for the owner: what the conversation is, and what \
ties it to the project. `cited_mails` are the numbers of the mails of this conversation you \
relied on. `confidence` is how sure you are, from 0 to 1.

What your tools return is content from outside too, fenced the same way.

"""

# The searches a run reads the company's own mail, projects and invoices through. Every one only
# reads, and only the desk's company. The assistant's runs read through them too
# (``linking.service.reading_tools``): one way of looking, whoever looks.
reading_tools: FunctionToolset[Desk] = FunctionToolset()

linker: Agent[Desk, Placing] = Agent(
    name=LINKER,
    deps_type=Desk,
    output_type=Placing,
    instructions=_INSTRUCTIONS + llm.OUTSIDE_DATA,
    retries=2,
    model_settings=ModelSettings(timeout=180),
    toolsets=[reading_tools],
)


@linker.output_validator
def _names_only_what_it_was_shown(context: RunContext[Desk], placing: Placing) -> Placing:
    shown = context.deps.projects
    named = [placing.project or "", *placing.alternatives]
    if placing.decision in ("link", "ask") and any(
        bare(number) in context.deps.refused for number in named
    ):
        raise ModelRetry(
            "The owner already said this conversation does not belong to that project. Choose "
            "another, or answer new_project or unplaced."
        )
    if placing.decision == "link" and (
        placing.project is None or bare(placing.project) not in shown
    ):
        raise ModelRetry(
            "For link, `project` must be the number of a project a tool or the conversation "
            "showed you. Search for it, or answer new_project or unplaced."
        )
    if placing.decision == "ask":
        known = {bare(number) for number in placing.alternatives if bare(number) in shown}
        if len(known) < 2:
            raise ModelRetry(
                "For ask, `alternatives` must hold the numbers of at least two projects you were "
                "shown."
            )
    if placing.decision == "new_project" and (
        placing.new_project is None or not placing.new_project.name.strip()
    ):
        raise ModelRetry("For new_project, fill `new_project` with at least its name.")
    return placing


def case(
    conversation: str, held: Sequence[str], sharing: Sequence[str], refused: Sequence[str]
) -> list[UserContent]:
    """The user turn for ``linker``: the conversation to place, the projects its earlier mails
    are tied to, those code found sharing a number with it, and those the owner already said it
    does not belong to; all of it fenced, since a project's name comes from mail too."""
    parts = [conversation]
    if held:
        parts.append("Earlier mails of this conversation are tied to:\n" + "\n".join(held))
    if sharing:
        parts.append(
            "Projects that share a number with this conversation, to start from:\n"
            + "\n".join(sharing)
        )
    if refused:
        parts.append(
            "The owner already said this conversation does not belong to:\n" + "\n".join(refused)
        )
    return llm.fenced("the mail conversation to place", ["\n\n".join(parts)])


# What the model is shown


async def conversation_text(db: AsyncSession, company_id: uuid.UUID, thread: ThreadRead) -> str:
    """A conversation as the model reads it: each mail with who wrote it and when, its files by
    name with the line a reader left on each (never a file's own text, ADR-0013), the numbers
    code read in it, and what its writer typed."""
    mails = list(enumerate(thread.mails, start=1))
    if len(mails) > _FIRST_MAILS + _LAST_MAILS:
        skipped = len(mails) - _FIRST_MAILS - _LAST_MAILS
        mails = mails[:_FIRST_MAILS] + mails[-_LAST_MAILS:]
    else:
        skipped = 0
    file_ids = [file.document_id for _, mail in mails for file in mail.files]
    files = await documents.get_documents(db, company_id, file_ids)
    shown_kinds = extractors.key_kinds() | _SHOWN_KINDS
    read: dict[uuid.UUID, list[str]] = {}
    for kind, ids in (
        (ItemKind.MESSAGE, [mail.id for _, mail in mails]),
        (ItemKind.DOCUMENT, file_ids),
    ):
        for fact in await facts.facts_of_items(
            db, company_id=company_id, item_kind=kind, item_ids=ids
        ):
            if fact.kind in shown_kinds:
                read.setdefault(fact.item_id, []).append(f"{fact.kind} {fact.value}")
    parts = []
    for number, mail in mails:
        sender = " ".join(part for part in (mail.sender_name, f"<{mail.sender}>") if part)
        lines = [
            f"[mail {number}] {mail.sent_at:%Y-%m-%d} From: {sender}"
            + (" (the company itself)" if mail.outbound else ""),
            f"Subject: {mail.subject or ''}",
        ]
        for file in mail.files:
            document = files.get(file.document_id)
            notes = (
                [note for note in (document.doc_type, document.description) if note]
                if document
                else []
            )
            notes += read.get(file.document_id, [])
            lines.append(f"File: {file.filename}" + (f" ({'; '.join(notes)})" if notes else ""))
        if mail.id in read:
            lines.append("Read by code: " + "; ".join(read[mail.id]))
        lines.append(mail.text[:_MAIL_TEXT].strip())
        parts.append("\n".join(lines))
        if skipped and number == _FIRST_MAILS:
            parts.append(f"({skipped} mails in between are not shown)")
    return "\n\n---\n\n".join(parts)


async def _summaries(db: AsyncSession, desk: Desk, threads: Sequence[ThreadRead]) -> list[str]:
    """A line per conversation, with the handle to open it by and the projects its mails are
    tied to."""
    mail_ids = [mail.id for thread in threads for mail in thread.mails]
    links = await projects.links_of_items(db, desk.company_id, ItemKind.MESSAGE, mail_ids)
    tied = [link for link in links if link.status is not LinkStatus.REJECTED]
    known = await projects.get_projects(db, desk.company_id, [link.project_id for link in tied])
    lines = []
    for thread in threads:
        if not thread.mails:
            continue
        ids = {mail.id for mail in thread.mails}
        numbers = []
        for link in tied:
            project = known.get(link.project_id)
            if (
                link.item_id in ids
                and project is not None
                and project.status is not ProjectStatus.REJECTED
            ):
                desk.show(project.number, project.id)
                if project.number not in numbers:
                    numbers.append(project.number)
        people = dict.fromkeys(
            mail.sender_name or mail.sender or "" for mail in thread.mails if not mail.outbound
        )
        first, last = thread.mails[0], thread.mails[-1]
        others = ", ".join(person for person in people if person) or "nobody else"
        lines.append(
            f"[c{desk.handle(thread.id)}] {first.sent_at:%Y-%m-%d} to {last.sent_at:%Y-%m-%d}, "
            f"{len(thread.mails)} mails, with {others}: "
            f"{first.subject or '(no subject)'}"
            + (f" (in project {', '.join(numbers)})" if numbers else " (in no project)")
        )
    return lines


def _day(value: date | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value + timedelta(days=1) if end else value, time.min, tzinfo=UTC)


# The reading tools: every one only reads, and only this company's data (the desk's company).


@reading_tools.tool
async def search_mail(
    context: RunContext[Desk], words: str, since: date | None = None, until: date | None = None
) -> str:
    """Searches the company's mail conversations. Every word must be in one mail of a
    conversation: in its subject, sender, recipients, file names or text. A number or a plate
    matches however it is spaced. `since` and `until` bound the mails' dates. Returns up to
    eight conversations, most recent first, each with a handle such as c3 to open it by."""
    desk = context.deps
    async with open_session() as db:
        found = await conversations.search_threads(
            db,
            desk.company_id,
            q=words,
            since=_day(since),
            until=_day(until, end=True),
            limit=_FOUND + 1,
        )
        lines = await _summaries(db, desk, [t for t in found if t.id != desk.thread_id][:_FOUND])
    return llm.fenced_text("mail search results", "\n".join(lines) or "No conversation matches.")


@reading_tools.tool
async def open_conversation(context: RunContext[Desk], conversation: int) -> str:
    """Opens a conversation a search or a project showed, by the number in its handle (3 for
    c3): its mails with their senders, dates, files and text."""
    desk = context.deps
    thread_id = desk.conversations.get(conversation)
    if thread_id is None:
        return "No such conversation: use a handle that search_mail or open_project returned."
    async with open_session() as db:
        thread = await conversations.read_thread(db, desk.company_id, thread_id)
        if thread is None:
            return "That conversation is gone."
        text = await conversation_text(db, desk.company_id, thread)
    desk.opened.append((CONVERSATION, thread_id))
    return llm.fenced_text(f"conversation c{conversation}", text)


@reading_tools.tool
async def search_projects(context: RunContext[Desk], words: str = "") -> str:
    """Searches the company's projects by a word of their name or by a number they have had;
    with no words, lists the most recently changed. Returns up to ten, each with its number."""
    desk = context.deps
    async with open_session() as db:
        found = await projects.find_projects(db, desk.company_id, q=words or None, limit=10)
    for project in found:
        desk.show(project.number, project.id)
    lines = [f"{project.number}: {project.name} ({project.status})" for project in found]
    return llm.fenced_text("project search results", "\n".join(lines) or "No project matches.")


@reading_tools.tool
async def open_project(context: RunContext[Desk], number: str) -> str:
    """Opens a project by its number: its name and status, the conversations and the invoices
    tied to it."""
    desk = context.deps
    async with open_session() as db:
        project = await projects.find_by_number(db, desk.company_id, number)
        if project is None or project.status is ProjectStatus.REJECTED:
            return "No such project: use a number that a tool returned."
        desk.show(project.number, project.id)
        desk.opened.append((PROJECT, project.id))
        links = await projects.links_of_project(db, desk.company_id, project.id)
        mails = [link.item_id for link in links if link.item_kind is ItemKind.MESSAGE]
        invoices = await books.get_invoices(
            db,
            desk.company_id,
            [link.item_id for link in links if link.item_kind is ItemKind.INVOICE],
        )
        threads = await conversations.threads_holding(db, desk.company_id, mails, limit=_FOUND)
        lines = [f"{project.number}: {project.name} ({project.status})", "Conversations:"]
        lines += await _summaries(db, desk, threads) or ["none"]
    lines += ["Invoices:", *(_invoice_line(invoice, []) for invoice in invoices)]
    if not invoices:
        lines.append("none")
    return llm.fenced_text(f"project {project.number}", "\n".join(lines))


@reading_tools.tool
async def search_invoices(
    context: RunContext[Desk], words: str = "", since: date | None = None, until: date | None = None
) -> str:
    """Searches the invoices in the company's accounting records by a part of their number or
    of the other party's name, issued between `since` and `until`. Returns up to ten, newest
    first, each with the projects it is tied to."""
    desk = context.deps
    async with open_session() as db:
        found = await books.find_invoices(
            db, desk.company_id, q=words or None, since=since, until=until, limit=10
        )
        links = await projects.links_of_items(
            db, desk.company_id, ItemKind.INVOICE, [invoice.id for invoice in found]
        )
        tied = [link for link in links if link.status is not LinkStatus.REJECTED]
        known = await projects.get_projects(db, desk.company_id, [link.project_id for link in tied])
    lines = []
    for invoice in found:
        numbers = []
        for link in tied:
            project = known.get(link.project_id)
            if (
                link.item_id == invoice.id
                and project is not None
                and project.status is not ProjectStatus.REJECTED
            ):
                desk.show(project.number, project.id)
                numbers.append(project.number)
        lines.append(_invoice_line(invoice, numbers))
    return llm.fenced_text("invoice search results", "\n".join(lines) or "No invoice matches.")


def _invoice_line(invoice: InvoiceOut, numbers: Sequence[str]) -> str:
    amount = (
        f"{invoice.total_amount} {invoice.currency or ''}".strip()
        if invoice.total_amount is not None
        else "no amount"
    )
    return (
        f"{invoice.kind} invoice {invoice.number or '(no number)'}, {invoice.direction}, "
        f"{invoice.party_name or 'unknown party'}, issued {invoice.issue_date or 'unknown'}, "
        f"{amount}" + (f" (in project {', '.join(numbers)})" if numbers else "")
    )
