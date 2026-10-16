"""The assistant's two ways of looking (ADR-0025): ``search`` over every kind of record the company
keeps at once, and ``open`` of one record by the handle a search or another record gave it.

Both only read, and only the run's company. What they return comes from outside (mail, file
names, a firm's name from the books), so it is fenced as data. ``open`` gives a file's type, what
its reader saw and the numbers code read in it, never its text (ADR-0013): ``read_file``, a
helper with no tools, answers a question from its pages.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal, get_args

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant import repository
from app.modules.assistant.seat import Seat
from app.modules.assistant.tools import bill_line, how, ids_of, truck_names
from app.modules.books import service as books
from app.modules.books.models import ReadKind
from app.modules.books.schemas import InvoiceLineOut
from app.modules.conversations import service as conversations
from app.modules.documents import service as documents
from app.modules.facts import service as facts
from app.modules.linking import service as linking
from app.modules.memory import service as memory
from app.modules.parties import service as parties
from app.modules.projects import checklist
from app.modules.projects import service as projects
from app.modules.projects.models import LinkStatus, ProjectStatus
from app.platform import llm
from app.platform.db import open_session
from app.platform.errors import DomainError
from app.platform.items import ItemKind
from app.platform.pagination import PageParams

type Source = Literal["mail", "projects", "invoices", "files", "firms", "memory", "chats"]
SOURCES: Final[tuple[Source, ...]] = get_args(Source.__value__)
_TITLES: Final[dict[Source, str]] = {
    "mail": "Mail conversations",
    "projects": "Projects",
    "invoices": "Invoices in the accounting records",
    "files": "Files",
    "firms": "Firms and people",
    "memory": "The company's memory",
    "chats": "Past chats",
}
# Hits a source gives when several are searched, and when it is the only one.
_EACH: Final = 5
_ONLY: Final = 10
# What a file's facts and a summary's line are shown of, at most.
_FACTS_SHOWN: Final = 30
_SUMMARY_LINE: Final = 240
# The handles a run gives: a conversation, a file, an invoice, a firm, a past chat. Anything else
# is read as a project's number.
_HANDLE: Final = re.compile(r"([cfips])(\d+)")
# The conversations of a project `open` lists.
_CONVERSATIONS: Final = 8

logger = logging.getLogger(__name__)

looking: FunctionToolset[Seat] = FunctionToolset()


@looking.tool
async def search(
    context: RunContext[Seat],
    query: str,
    sources: list[Source] | None = None,
    since: date | None = None,
    until: date | None = None,
) -> str:
    """Searches the company's records for `query`, in every source at once unless `sources`
    names some: mail (conversations by subject, sender, file names and text), projects (by name
    or a number they had), invoices (the accounting records, by number or the other party's
    name), files (by name, type or what their reader saw), firms (firms and people, from the
    books and from mail), memory (what the owner taught Tamga) and chats (summaries of past
    chats). Every word must be found; a number or a plate matches however it is spaced. `since`
    and `until` bound the dates of mail and invoices. An empty query lists the most recent of
    each source. Returns a few hits from each source, each with a handle for `open`: c3 for a
    conversation, a project's number, i2 an invoice, f4 a file, p1 a firm, s2 a past chat."""
    seat = context.deps
    chosen = list(dict.fromkeys(sources or SOURCES))
    each = _ONLY if len(chosen) == 1 else _EACH
    # Each source on a session of its own, all at once (ADR-0025, point 3). One that fails says
    # so and leaves the others' hits.
    found = await asyncio.gather(
        *(_SEARCHES[source](seat, query.strip(), since, until, each) for source in chosen),
        return_exceptions=True,
    )
    parts = []
    for source, lines in zip(chosen, found, strict=True):
        if isinstance(lines, BaseException):
            # The error's type only: it can quote what was searched.
            logger.warning("searching %s failed: %s", source, type(lines).__name__)
            parts.append(f"{_TITLES[source]}:\ncould not be searched just now")
        else:
            parts.append(f"{_TITLES[source]}:\n" + ("\n".join(lines) or "nothing matches"))
    return llm.fenced_text("search results", "\n\n".join(parts))


@looking.tool(name="open")
async def open_record(context: RunContext[Seat], handle: str) -> str:
    """Opens one record by the handle a search or another record gave it: a conversation (c3)
    with its mails, their writers, dates, files and text; a project, by its number, with its
    trucks, conversations, files and invoices, how each was tied to it, and what it still lacks
    (each with a handle the draft tools take: t1, l2); an invoice (i2) with its amounts, what is
    left to pay and its projects; a file (f4): its type, what its reader saw in it and the
    numbers read in it, never its text, so ask read_file a question about what it says; a firm
    (p1) with what identifies it, its balance and its latest invoices; a past chat (s2), its
    summary. A long record is cut short: investigate reads a whole job's mail for a question."""
    seat = context.deps
    handle = handle.strip()
    given = _given(seat, handle)
    if given is not None:
        kind, record_id = given
        text = await _OPENERS[kind](seat, record_id, handle)
    else:
        # Not a handle this run was given: a project's number, which reaches the company's
        # projects as the app's search does.
        found = await _project(seat, handle)
        if found is None:
            return (
                f"No such record: {handle} is neither a handle a tool gave in this chat nor a "
                "project's number."
            )
        text = found
    if len(text) > seat.open_cap:
        text = text[: seat.open_cap] + (
            f"\n(cut here, at {seat.open_cap} characters: investigate reads all of it)"
        )
    seat.read[key(handle)] = text
    # The app's view of it rides on the fence's label, which the app reads to make the step a
    # link: an id the owner never sees, and nothing the model is asked to use.
    view = _VIEWS.get(given[0]) if given is not None else None
    shown = f"{view}:{given[1]}" if view and given else seat.views.get(key(handle))
    return llm.fenced_text(f"record {handle}" + (f" · view {shown}" if shown else ""), text)


def key(handle: str) -> str:
    """What a record a run opened is kept under, and a helper's citation looks it up by: a
    handle in lower case, a project's number as the run's desk keys it."""
    handle = handle.strip()
    return handle.lower() if _HANDLE.fullmatch(handle.lower()) else linking.bare(handle)


def _given(seat: Seat, handle: str) -> tuple[str, uuid.UUID] | None:
    """The record behind a handle this run was given, whatever its case."""
    match = _HANDLE.fullmatch(handle.lower())
    if match is None:
        return None
    kind, number = match[1], int(match[2])
    record_id = seat.conversations.get(number) if kind == "c" else seat.look(kind, number)
    return None if record_id is None else (kind, record_id)


# Searching, a source each: lines with their handles


async def _mail(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        found = await conversations.search_threads(
            db, seat.company_id, q=q, since=_day(since), until=_day(until, end=True), limit=limit
        )
        return await linking.summaries(db, seat, found)


async def _projects(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        found = await projects.find_projects(db, seat.company_id, q=q or None, limit=limit)
    lines = []
    for project in found:
        seat.show(project.number, project.id)
        days = " to ".join(str(day) for day in (project.starts_on, project.ends_on) if day)
        lines.append(
            f"{project.number}: {project.name} ({project.status})" + (f", {days}" if days else "")
        )
    return lines


async def _invoices(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        found = await books.read_invoices(
            db,
            seat.company_id,
            await books.find_invoices(
                db, seat.company_id, q=q or None, since=since, until=until, limit=limit
            ),
        )
        held = await _projects_holding(db, seat, ItemKind.INVOICE, [bill.id for bill in found])
    return [
        f"{seat.name('i', bill.id)}: {bill_line(bill)}" + _in(held.get(bill.id, []))
        for bill in found
    ]


async def _files(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        found = await documents.find_documents(db, seat.company_id, q, limit)
        held = await _projects_holding(db, seat, ItemKind.DOCUMENT, [file.id for file in found])
    lines = []
    for file in found:
        notes = [note for note in (file.doc_type, file.description) if note]
        lines.append(
            f"{seat.name('f', file.id)}: {file.filename}"
            + (f" ({'; '.join(notes)})" if notes else "")
            + _in(held.get(file.id, []))
        )
    return lines


async def _firms(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        page = await parties.list_parties(
            db,
            seat.actor,
            kind=None,
            q=q or None,
            sort="source",
            page=PageParams(limit=limit, offset=0),
        )
    return [
        f"{seat.name('p', party.id)}: {party.name} ({party.kind}, "
        + (f"from {party.source})" if party.source else "from mail or by hand)")
        for party in page.items
    ]


async def _memory(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        found = await memory.recall(db, seat.company_id, q)
        await db.commit()
    return [
        f"- {seat.name('m', entry.id)} {entry.kind.value}: {entry.text}" for entry in found[:limit]
    ]


async def _chats(
    seat: Seat, q: str, since: date | None, until: date | None, limit: int
) -> list[str]:
    async with open_session() as db:
        found = await repository.find_summaries(
            db, seat.company_id, q, leaving_out=seat.chat_id, limit=limit
        )
    lines = []
    for chat in found:
        line = " ".join(chat.summary.split())
        if len(line) > _SUMMARY_LINE:
            line = line[:_SUMMARY_LINE].rsplit(" ", 1)[0] + " …"
        when = f"{chat.last_message_at:%Y-%m-%d}" if chat.last_message_at else ""
        lines.append(f"{seat.name('s', chat.id)}: {when} {chat.title or '(no title)'}: {line}")
    return lines


type _Search = Callable[[Seat, str, date | None, date | None, int], Awaitable[list[str]]]
_SEARCHES: Final[dict[Source, _Search]] = {
    "mail": _mail,
    "projects": _projects,
    "invoices": _invoices,
    "files": _files,
    "firms": _firms,
    "memory": _memory,
    "chats": _chats,
}


# Opening, a kind each


async def _conversation(seat: Seat, thread_id: uuid.UUID, handle: str) -> str:
    async with open_session() as db:
        thread = await conversations.read_thread(db, seat.company_id, thread_id)
        if thread is None:
            return "That conversation is gone."
        text = await linking.conversation_text(db, seat.company_id, thread)
    seat.opened.append((linking.CONVERSATION, thread_id))
    if thread.mails:
        seat.views[key(handle)] = f"mail:{thread.mails[-1].id}"
    # Its files by handle, for open and read_file: the text above names them only.
    files = list(dict.fromkeys(file.document_id for mail in thread.mails for file in mail.files))
    if files:
        names = {file.document_id: file.filename for mail in thread.mails for file in mail.files}
        text += "\n\nIts files: " + "; ".join(
            f"{seat.name('f', document_id)} {names[document_id]}" for document_id in files
        )
    return text


async def _project(seat: Seat, number: str) -> str | None:
    async with open_session() as db:
        project = await projects.find_by_number(db, seat.company_id, number)
        if project is None or project.status is ProjectStatus.REJECTED:
            return None
        seat.show(project.number, project.id)
        seat.opened.append((linking.PROJECT, project.id))
        links = await projects.links_of_project(db, seat.company_id, project.id)
        files = await documents.get_documents(db, seat.company_id, ids_of(links, ItemKind.DOCUMENT))
        bills = {
            bill.id: bill
            for bill in await books.read_invoices(
                db,
                seat.company_id,
                await books.get_invoices(db, seat.company_id, ids_of(links, ItemKind.INVOICE)),
            )
        }
        items = await checklist.list_items(db, seat.actor, [project.id])
        trucks = list(
            dict.fromkeys(
                [item.shipment_id for item in items if item.shipment_id is not None]
                + [link.shipment_id for link in links if link.shipment_id is not None]
            )
        )
        named = await truck_names(db, seat.company_id, trucks)
        mails = ids_of(links, ItemKind.MESSAGE)
        threads = await conversations.threads_holding(
            db, seat.company_id, mails, limit=_CONVERSATIONS
        )
        talks = await linking.summaries(db, seat, threads)
    days = " to ".join(str(day) for day in (project.starts_on, project.ends_on) if day)
    lines = [f"{project.number}: {project.name} ({project.status})" + (f", {days}" if days else "")]
    lines.append("Trucks:")
    lines += [f"{seat.name('t', truck)}: {named.get(truck, 'a truck')}" for truck in trucks] or [
        "none"
    ]
    lines.append(f"Conversations ({len(mails)} mails in all):")
    lines += talks or ["none"]
    lines.append("Files:")
    shown = [
        link for link in links if link.item_kind is ItemKind.DOCUMENT and link.item_id in files
    ]
    for link in shown:
        file = files[link.item_id]
        kind = f" ({file.doc_type})" if file.doc_type else ""
        lines.append(
            f"[{seat.name('l', link.id)}] {seat.name('f', file.id)} {file.filename}{kind}, "
            + how(seat, link)
        )
    if not shown:
        lines.append("none")
    lines.append("Invoices:")
    billed = [
        link for link in links if link.item_kind is ItemKind.INVOICE and link.item_id in bills
    ]
    for link in billed:
        bill = bills[link.item_id]
        lines.append(
            f"[{seat.name('l', link.id)}] {seat.name('i', bill.id)} {bill_line(bill)}, "
            + how(seat, link)
        )
    if not billed:
        lines.append("none")
    for state, title in (("missing", "Still missing"), ("waiting", "Waiting")):
        wanted = [
            item.key.replace("_", " ")
            + (f" on {seat.name('t', item.shipment_id)}" if item.shipment_id else "")
            for item in items
            if item.state == state
        ]
        if wanted:
            lines.append(f"{title}: " + "; ".join(wanted))
    return "\n".join(lines)


async def _invoice(seat: Seat, invoice_id: uuid.UUID, handle: str) -> str:
    async with open_session() as db:
        try:
            bill = await books.get_invoice(db, seat.actor, invoice_id)
        except DomainError:
            return "That invoice is gone."
        held = await _projects_holding(db, seat, ItemKind.INVOICE, [bill.id])
        charged = await books.invoice_lines(db, seat.actor, [bill.id])
    lines = [bill_line(bill)]
    if bill.returns:
        lines.append(
            "A return, not a bill to pay"
            + (f" (the books call it {bill.type_name})" if bill.type_name else "")
        )
    elif bill.type_name:
        lines.append(f"The books call it: {bill.type_name}")
    if not bill.returns and bill.read_kind in (ReadKind.RETURN, ReadKind.CREDIT):
        kind = "return" if bill.read_kind is ReadKind.RETURN else "credit note"
        lines.append(f"Its file reads as a {kind} (the bill reader's word; the books do not say)")
    if bill.e_document_id:
        lines.append(f"E-document: {bill.e_document_id}")
    if bill.due_date:
        lines.append(f"Due: {bill.due_date}")
    if bill.payment_date:
        lines.append(f"Paid on: {bill.payment_date}")
    if bill.net_amount is not None:
        lines.append(f"Net {bill.net_amount}, tax {bill.tax_amount}")
    if bill.account_owed is not None:
        lines.append(
            f"The firm's account in the books shows {bill.account_owed} open on it"
            + (f" as of {bill.account_on}" if bill.account_on else "")
        )
    if bill.description:
        lines.append(f"Written in the books as: {bill.description}")
    if bill.about:
        lines.append(f"What its file says it is for: {bill.about}")
    if charged:
        lines.append(
            "What it charges for, line by line, as read from its file"
            + (" (the lines do not add up to the bill)" if bill.lines_balance is False else "")
            + ":"
        )
        lines.extend(_charged(line) for line in charged)
    lines.append("Projects: " + (", ".join(held.get(bill.id, [])) or "none"))
    return "\n".join(lines)


def _charged(line: InvoiceLineOut) -> str:
    parts = [line.charge]
    if line.amount is not None:
        parts.append(f"{line.amount} {line.currency or ''}".strip())
    if line.days is not None:
        parts.append(f"{line.days} days")
    if line.origin or line.destination:
        parts.append(f"{line.origin or '?'} → {line.destination or '?'}")
    if line.plates:
        parts.append("plates " + ", ".join(line.plates))
    return f'- {", ".join(parts)}: "{line.quote}"'


async def _file(seat: Seat, document_id: uuid.UUID, handle: str) -> str:
    async with open_session() as db:
        document = (await documents.get_documents(db, seat.company_id, [document_id])).get(
            document_id
        )
        if document is None:
            return "That file is gone."
        read = await facts.facts_of(
            db, company_id=seat.company_id, item_kind=ItemKind.DOCUMENT, item_id=document_id
        )
        carrier = (await conversations.first_carriers(db, seat.company_id, [document_id])).get(
            document_id
        )
        mail = (
            (await conversations.message_summaries(db, seat.company_id, [carrier.message_id])).get(
                carrier.message_id
            )
            if carrier
            else None
        )
        links = [
            link
            for link in await projects.links_of_items(
                db, seat.company_id, ItemKind.DOCUMENT, [document_id]
            )
            if link.status is not LinkStatus.REJECTED
        ]
        held = await projects.get_projects(db, seat.company_id, [link.project_id for link in links])
    lines = [
        document.filename,
        "Type: "
        + (
            f"{document.doc_type} (set by {document.doc_type_source})"
            if document.doc_type
            else "not known"
        ),
        "Read: "
        + (document.read_state or "not yet")
        + (f" ({document.unreadable_reason})" if document.unreadable_reason else "")
        + (f", {document.parts} pages" if document.parts else ""),
    ]
    if document.description:
        lines.append(f"Its reader saw: {document.description}")
    if read:
        lines.append(
            "Read by code: "
            + "; ".join(f"{fact.kind} {fact.value}" for fact in read[:_FACTS_SHOWN])
        )
    if mail:
        lines.append(
            f"Came with the mail of {mail.sent_at:%Y-%m-%d} from "
            f"{mail.sender_name or mail.sender or 'unknown'}: {mail.subject or '(no subject)'}"
        )
    else:
        lines.append("Uploaded, not brought by a mail")
    for link in links:
        project = held.get(link.project_id)
        if project is not None:
            seat.show(project.number, project.id)
            lines.append(f"In project {project.number}: {how(seat, link)}")
    return "\n".join(lines)


async def _firm(seat: Seat, party_id: uuid.UUID, handle: str) -> str:
    async with open_session() as db:
        party = (await parties.get_parties(db, seat.company_id, [party_id])).get(party_id)
        if party is None:
            return "That firm is gone."
        known = await parties.get_identifiers(db, seat.company_id, party_id)
        latest = await books.list_invoices(
            db,
            seat.actor,
            books.InvoiceFilter(party_id=party_id),
            page=PageParams(limit=_EACH, offset=0),
        )
    lines = [
        f"{party.name} ({party.kind}, "
        + (f"from {party.source})" if party.source else "from mail or by hand)")
    ]
    lines += [
        f"{each.kind} {each.value}" + ("" if each.confirmed else " (not confirmed)")
        for each in known
    ]
    if party.balance is not None:
        side = "owes the company" if party.balance > 0 else "the company owes it"
        lines.append(
            f"Balance in the books: {abs(party.balance)} {party.balance_currency or ''} ({side})"
            + (f", as of {party.balance_on}" if party.balance_on else "")
        )
    lines.append(f"Latest invoices ({latest.total} in all):")
    lines += [f"{seat.name('i', bill.id)}: {bill_line(bill)}" for bill in latest.items] or ["none"]
    return "\n".join(lines)


async def _past_chat(seat: Seat, chat_id: uuid.UUID, handle: str) -> str:
    async with open_session() as db:
        chat = await repository.get_chat(db, seat.company_id, chat_id)
    if chat is None or not chat.summary:
        return "That chat is gone."
    when = f"{chat.last_message_at:%Y-%m-%d}" if chat.last_message_at else ""
    return f"{chat.title or '(no title)'}, last written {when}\n{chat.summary}"


type _Opener = Callable[[Seat, uuid.UUID, str], Awaitable[str]]
# The app's canvas kind of each record a handle opens; a conversation's is its last mail's.
_VIEWS: Final[dict[str, str]] = {"i": "invoice", "f": "document"}

_OPENERS: Final[dict[str, _Opener]] = {
    "c": _conversation,
    "i": _invoice,
    "f": _file,
    "p": _firm,
    "s": _past_chat,
}


async def _projects_holding(
    db: AsyncSession, seat: Seat, kind: ItemKind, item_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """The numbers of the projects each item is tied to and not rejected from."""
    links = [
        link
        for link in await projects.links_of_items(db, seat.company_id, kind, item_ids)
        if link.status is not LinkStatus.REJECTED
    ]
    known = await projects.get_projects(db, seat.company_id, [link.project_id for link in links])
    held: dict[uuid.UUID, list[str]] = {}
    for link in links:
        project = known.get(link.project_id)
        if project is not None and project.status is not ProjectStatus.REJECTED:
            seat.show(project.number, project.id)
            numbers = held.setdefault(link.item_id, [])
            if project.number not in numbers:
                numbers.append(project.number)
    return held


def _in(numbers: Sequence[str]) -> str:
    return f" (in project {', '.join(numbers)})" if numbers else ""


def _day(value: date | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value + timedelta(days=1) if end else value, time.min, tzinfo=UTC)
