import email.message
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.messages import UserPromptPart as UserPrompt
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select, update
from sqlalchemy import text as sql

from app.config import get_settings
from app.modules.books.models import Invoice, InvoiceDirection, InvoiceKind
from app.modules.conversations import repository as mail_repository
from app.modules.conversations import service as conversations
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.conversations.models import (
    MailFolder,
    MailModelRead,
    Message,
    Thread,
    ThreadCategory,
)
from app.modules.conversations.service import ThreadRead
from app.modules.documents import service as documents
from app.modules.linking import agents as linking_agents
from app.modules.linking import repository, service
from app.modules.linking.models import Outcome, Placement
from app.modules.projects import service as projects
from app.modules.projects.models import (
    FieldSource,
    LinkMethod,
    LinkStatus,
    Project,
    ProjectLink,
    ProjectStatus,
)
from app.platform import llm
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.platform.items import ItemKind
from app.tests.conftest import guest_client
from app.tests.documents import pdfs

# The mail these tests store goes to the storage service, in the bucket the tests make.
pytestmark = pytest.mark.usefixtures("storage_bucket")

OWN = "ops@acme.example"
QUOTE_ONLY = "On Mon, 3 Aug 2026 at 09:00, Ada Stone wrote:\n> the quoted history"
CUSTOMER = "Ada Stone <ada@stone.example>"
TOOLS = {"search_mail", "open_conversation", "search_projects", "open_project", "search_invoices"}

type Step = tuple[str, dict[str, Any]] | Exception


def mail(
    message_id: str,
    *,
    sender: str = CUSTOMER,
    subject: str = "Munich load",
    body: str = "We confirm the load for Monday, steel coils from Istanbul to Munich.",
    date: str = "Mon, 03 Aug 2026 09:00:00 +0300",
    references: str | None = None,
    files: tuple[str, ...] = (),
    attach: dict[str, bytes] | None = None,
    to: str | None = None,
    cc: str | None = None,
) -> bytes:
    message = email.message.EmailMessage()
    message["From"] = sender
    if to:
        message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    message["Date"] = date
    message["Message-ID"] = message_id
    if references:
        message["References"] = references
    message.set_content(body)
    for name, content in ({name: b"%PDF-1.7 a" for name in files} | (attach or {})).items():
        message.add_attachment(content, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()


async def arrive(
    company_id: uuid.UUID, uid: int, raw: bytes, *, category: ThreadCategory | None
) -> None:
    """Stores a mail, processes it, and sorts its conversation as ``category`` when it waits."""
    async with open_session() as db:
        stored = await conversations.ingest_mail(
            db,
            company_id=company_id,
            place=conversations.FolderPlace(mailbox=OWN, folder="INBOX", uidvalidity=1, sent=False),
            fetched=FetchedMail(uid=uid, raw=raw, internaldate=None),
            own_addresses={OWN},
        )
        await db.commit()
    assert stored
    await conversations.process_pending()
    if category is None:
        return
    async with open_session() as db:
        keys = await db.scalars(
            select(Thread.thread_key).where(
                Thread.company_id == company_id, Thread.category.is_(None)
            )
        )
        for key in keys.all():
            await mail_repository.set_sorting(
                db, company_id, key, category=category, sorted_by="test", reason="", at=utcnow()
            )
        await db.commit()


class Script:
    """A stand-in for the model that plays its steps, a request each, and keeps what it was
    sent."""

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[list[ModelMessage]] = []
        self.infos: list[AgentInfo] = []

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(list(messages))
        self.infos.append(info)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        name, args = step
        if name == "text":
            return ModelResponse(parts=[TextPart(content=args["content"])])
        if name == "answer":
            name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])

    def prompt(self) -> str:
        first = self.requests[0][0]
        assert isinstance(first, ModelRequest)
        return "".join(
            item
            for part in first.parts
            if isinstance(part, UserPrompt)
            for item in part.content
            if isinstance(item, str)
        )

    def returned(self, request: int) -> str:
        """What the tool called in the request before ``request`` gave back."""
        last = self.requests[request][-1]
        assert isinstance(last, ModelRequest)
        return "".join(str(part.content) for part in last.parts if isinstance(part, ToolReturnPart))


def tool(name: str, **args: Any) -> Step:
    return name, args


def text(content: str) -> Step:
    """What a model that answers in words, such as the titler, says."""
    return "text", {"content": content}


def answer(decision: str, **fields: Any) -> Step:
    return "answer", {"decision": decision, "reason": "why", "confidence": 0.8, **fields}


NEW_PROJECT = answer(
    "new_project",
    new_project={"origin": "Istanbul", "cargo": "steel coils"},
    cited_mails=[1],
)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Script]:
    """Puts a scripted stand-in in the model's place, with a budget of forty reads a day."""
    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 40)
    # Whatever a developer's own .env names: a test that wants a second model names it.
    monkeypatch.setattr(get_settings(), "agent_second_models", {})

    def install(*steps: Step) -> Script:
        script = Script(*steps)
        stand_in = FunctionModel(script.respond)
        monkeypatch.setattr(llm, "model_for", lambda name: stand_in)
        return script

    return install


async def placement(company_id: uuid.UUID, thread_key: str) -> Placement:
    async with open_session() as db:
        found = await db.scalar(
            select(Placement)
            .join(Thread, Thread.id == Placement.thread_id)
            .where(Placement.company_id == company_id, Thread.thread_key == thread_key)
        )
    assert found is not None
    return found


async def links(company_id: uuid.UUID, thread_key: str) -> list[ProjectLink]:
    """The links of a conversation's mails, in the order the mails were sent."""
    async with open_session() as db:
        rows = await db.scalars(
            select(ProjectLink)
            .join(Message, Message.id == ProjectLink.item_id)
            .where(
                ProjectLink.company_id == company_id,
                ProjectLink.item_kind == ItemKind.MESSAGE,
                Message.thread_key == thread_key,
            )
            .order_by(Message.sent_at, ProjectLink.created_at)
        )
        return list(rows.all())


async def name_projects(
    company_id: uuid.UUID, model: Callable[..., Script], *said: str
) -> list[Project]:
    """The titler's job on each project in the order they were made, the model saying each of
    ``said`` in turn; with nothing said it fails, and code names each by its job."""
    made = await the_projects(company_id)
    model(*([text(each) for each in said] or [RuntimeError("no model")] * len(made)))
    for project in made:
        await service.name_project(company_id, project.id)
    return await the_projects(company_id)


async def the_projects(company_id: uuid.UUID) -> list[Project]:
    async with open_session() as db:
        rows = await db.scalars(
            select(Project).where(Project.company_id == company_id).order_by(Project.created_at)
        )
        return list(rows.all())


async def reads(company_id: uuid.UUID) -> int:
    async with open_session() as db:
        rows = await db.scalars(
            select(MailModelRead.agent).where(MailModelRead.company_id == company_id)
        )
        return len([agent for agent in rows.all() if agent == "linker"])


async def owner_project(company_id: uuid.UUID, thread_key: str, name: str = "Munich") -> Project:
    """A project the owner made, with the conversation's mails tied to it and confirmed."""
    async with open_session() as db:
        project = await projects.new_project(
            db,
            company_id=company_id,
            name=name,
            status=ProjectStatus.DRAFT,
            source=FieldSource.OWNER,
        )
        mails = await db.scalars(
            select(Message.id).where(
                Message.company_id == company_id, Message.thread_key == thread_key
            )
        )
        for mail_id in mails.all():
            await projects.link_item(
                db,
                company_id=company_id,
                item_kind=ItemKind.MESSAGE,
                item_id=mail_id,
                project_id=project.id,
                method=LinkMethod.OWNER,
                confidence=1.0,
                confirm=True,
            )
        await db.commit()
        return project


@pytest.mark.integration
async def test_the_linker_proposes_a_project_and_ties_the_mails_as_suggestions(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    order = mail("<order@x>", files=("packing list.pdf",))
    await arrive(company_id, 1, order, category=ThreadCategory.WORK)
    script = model(tool("search_projects", words="Stone"), NEW_PROJECT)

    assert await service.place_pending() == 1
    assert await service.place_pending() == 0

    [project] = await the_projects(company_id)
    # Named by code until the titler names it: no client or route read, so who wrote and when.
    assert (project.name, project.status) == ("Ada Stone · 03.08.2026", ProjectStatus.PROPOSED)
    [link] = await links(company_id, "<order@x>")
    # ADR-0010: a model's reading proposes; only the owner confirms.
    assert (link.project_id, link.status, link.method) == (
        project.id,
        LinkStatus.SUGGESTED,
        LinkMethod.AGENT,
    )
    placed = await placement(company_id, "<order@x>")
    assert (placed.outcome, placed.placed_by, placed.project_id) == (
        Outcome.PROPOSED,
        "model:linker",
        project.id,
    )
    assert placed.reason == "why" and placed.cited == [link.item_id] and not placed.waiting
    assert placed.reading == {"origin": "Istanbul", "cargo": "steel coils"}
    # What gave it is kept with it, so a placement made under an older prompt can be found.
    assert placed.made_by == {
        "model": llm.model_name("linker"),
        "effort": llm.effort("linker"),
        "prompt": llm.fingerprint(linking_agents.linker),
    }
    # Both requests were sent the mail, and both count against the day's budget.
    assert await reads(company_id) == 2
    # The conversation and what a tool returns are fenced as data; the tools only read.
    prompt = script.prompt()
    marker = prompt.split()[3]
    assert prompt.startswith(f"BEGIN OUTSIDE DATA {marker}\n")
    assert prompt.rstrip().endswith(f"END OUTSIDE DATA {marker}")
    assert "steel coils from Istanbul to Munich" in prompt and "Ada Stone" in prompt
    assert "[mail 1] 2026-08-03" in prompt and "File: packing list.pdf" in prompt
    assert {definition.name for definition in script.infos[0].function_tools} == TOOLS
    assert script.returned(1).startswith("BEGIN OUTSIDE DATA ")


@pytest.mark.integration
async def test_a_second_conversation_about_the_job_is_found_by_search_and_linked(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    other = await guest_client(client)
    await arrive(other, 1, mail("<theirs@x>", subject="Munich, another company"), category=None)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)
    # Not a reply, and no number in common: only reading it ties it to the job.
    await arrive(
        company_id,
        2,
        mail("<driver@x>", subject="Truck for Munich", body="The driver is at the Munich dock."),
        category=ThreadCategory.WORK,
    )
    script = model(
        tool("search_mail", words="Munich"),
        tool("open_conversation", conversation=1),
        answer(
            "link",
            project=project.number,
            cited_mails=[1, 7],
            party=" Hauler  Ltd ",
            party_role="subcontractor",
        ),
    )

    assert await service.place_pending() == 1

    found = script.returned(1)
    # The other conversation of this company with the project it is in, and nothing of another's.
    assert "[c1]" in found and "Munich load" in found and f"in project {project.number}" in found
    assert "another company" not in found
    assert "steel coils from Istanbul to Munich" in script.returned(2)
    [link] = await links(company_id, "<driver@x>")
    assert (link.project_id, link.status, link.method) == (
        project.id,
        LinkStatus.SUGGESTED,
        LinkMethod.AGENT,
    )
    placed = await placement(company_id, "<driver@x>")
    # A mail number the conversation does not have is dropped, not guessed at.
    assert placed.outcome is Outcome.LINKED and placed.cited == [link.item_id]
    # Who the company deals with in it, and that it carries the job for the company.
    assert placed.reading == {"party": "Hauler Ltd", "party_role": "subcontractor"}
    assert await the_projects(other) == []


@pytest.mark.integration
async def test_a_project_the_run_was_never_shown_cannot_be_named(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    other = await guest_client(client)
    await arrive(other, 1, mail("<theirs@x>"), category=ThreadCategory.WORK)
    theirs = await owner_project(other, "<theirs@x>")
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    script = model(
        tool("open_project", number=theirs.number),
        answer("link", project=theirs.number),
        answer("unplaced"),
    )

    assert await service.place_pending() == 2  # theirs is whole already, by code

    assert script.returned(1).startswith("No such project")
    # The answer naming it failed the output's checks, and the model was asked again.
    assert len(script.requests) == 3
    assert await links(company_id, "<order@x>") == []
    assert (await placement(company_id, "<order@x>")).outcome is Outcome.UNPLACED
    assert [project.id for project in await the_projects(other)] == [theirs.id]


@pytest.mark.integration
async def test_a_shared_order_number_places_a_conversation_without_a_model(
    client: httpx.AsyncClient, model: Callable[..., Script], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 0)
    script = model()
    body = "Order no 4500123, steel coils."
    await arrive(company_id, 1, mail("<order@x>", body=body), category=ThreadCategory.WORK)
    project = await owner_project(company_id, "<order@x>")
    await arrive(
        company_id,
        2,
        mail("<cmr@x>", subject="CMR", body="Signed CMR attached. Order no 4500123."),
        category=ThreadCategory.WORK,
    )
    await arrive(
        company_id,
        3,
        mail("<bank@x>", subject="Transfer", body="Payment received."),
        category=ThreadCategory.AUTOMATED,
    )

    assert await service.place_pending() == 3

    [link] = await links(company_id, "<cmr@x>")
    assert (link.project_id, link.method, link.status) == (
        project.id,
        LinkMethod.KEY,
        LinkStatus.SUGGESTED,
    )
    # The link cites the fact that carries the number.
    assert len(link.evidence) == 1
    placed = await placement(company_id, "<cmr@x>")
    assert (placed.outcome, placed.placed_by) == (Outcome.LINKED, "keys")
    assert placed.made_by is None
    # The owner's own links stand as they were.
    [owned] = await links(company_id, "<order@x>")
    assert (owned.status, owned.method) == (LinkStatus.CONFIRMED, LinkMethod.OWNER)
    assert (await placement(company_id, "<order@x>")).placed_by == "thread"
    # A number in more places than one job has mails is no key: it ties nothing.
    others = service.KEY_MAX_ITEMS + 1
    for uid in range(10, 10 + others + 1):
        common = mail(f"<sig{uid}@x>", subject=f"Offer {uid}", body="Our ref: ACME-0001.")
        await arrive(company_id, uid, common, category=ThreadCategory.AUTOMATED)
    await owner_project(company_id, "<sig10@x>", "Another job")
    assert await service.place_pending() == others + 1
    assert (await placement(company_id, "<sig11@x>")).outcome is Outcome.UNPLACED
    # A system's notice with no number in common is left unplaced, and no model reads it.
    bank = await placement(company_id, "<bank@x>")
    assert (bank.outcome, bank.placed_by) == (Outcome.UNPLACED, "keys")
    assert script.requests == [] and await reads(company_id) == 0


@pytest.mark.integration
async def test_an_invoice_in_the_books_ties_the_mail_that_names_it(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    project = await owner_project(company_id, "<order@x>")
    async with open_session() as db:
        invoice = Invoice(
            company_id=company_id,
            source="test",
            source_id="1",
            direction=InvoiceDirection.RECEIVABLE,
            kind=InvoiceKind.SALES,
            number="FTR2026000000118",
            party_name="Stone GmbH",
            issue_date=date(2026, 8, 10),
            currency="EUR",
            total_amount=Decimal("1200.00"),
            raw={},
        )
        db.add(invoice)
        await db.flush()
        await projects.link_item(
            db,
            company_id=company_id,
            item_kind=ItemKind.INVOICE,
            item_id=invoice.id,
            project_id=project.id,
            method=LinkMethod.OWNER,
            confidence=1.0,
            confirm=True,
        )
        await db.commit()
    # A bank's notice names the invoice as people write it; the books hold it without the dashes.
    paid = mail("<paid@x>", subject="Transfer", body="Invoice no: FTR-2026-000000118 is paid.")
    await arrive(company_id, 2, paid, category=ThreadCategory.AUTOMATED)
    await arrive(company_id, 3, mail("<thanks@x>", subject="Thanks"), category=ThreadCategory.WORK)
    script = model(
        tool("search_invoices", words="Stone", since="2026-08-01"),
        tool("open_project", number=project.number),
        answer("link", project=project.number),
    )

    assert await service.place_pending() == 3

    [by_number] = await links(company_id, "<paid@x>")
    assert (by_number.project_id, by_number.method) == (project.id, LinkMethod.KEY)
    listed, opened = script.returned(1), script.returned(2)
    assert "sales invoice FTR2026000000118" in listed and "Stone GmbH" in listed
    assert "1200.00 EUR" in listed and f"in project {project.number}" in listed
    assert "Munich load" in opened and "FTR2026000000118" in opened
    [by_model] = await links(company_id, "<thanks@x>")
    assert (by_model.project_id, by_model.method) == (project.id, LinkMethod.AGENT)


@pytest.mark.integration
async def test_without_a_budget_a_conversation_that_needs_reading_waits(
    client: httpx.AsyncClient, model: Callable[..., Script], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 0)
    script = model(NEW_PROJECT)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    await arrive(company_id, 2, mail("<news@x>", subject="Offers"), category=ThreadCategory.NOISE)

    assert await service.place_pending() == 0

    waits = await placement(company_id, "<order@x>")
    assert waits.waiting and waits.outcome is None
    assert script.requests == []
    # Noise is not even noted.
    async with open_session() as db:
        noted = await db.scalars(select(Placement).where(Placement.company_id == company_id))
        assert len(noted.all()) == 1

    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 40)
    assert await service.place_pending() == 1


@pytest.mark.integration
async def test_new_mail_soon_follows_its_conversation_and_later_mail_is_read_again(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)

    script = model()
    soon = mail("<soon@x>", date="Wed, 05 Aug 2026 09:00:00 +0300", references="<order@x>")
    await arrive(company_id, 2, soon, category=None)
    assert await service.place_pending() == 1

    first, second = await links(company_id, "<order@x>")
    assert (second.project_id, second.method, second.status) == (
        project.id,
        LinkMethod.SCORED,
        LinkStatus.SUGGESTED,
    )
    placed = await placement(company_id, "<order@x>")
    # What the model said of the conversation is kept; the new mail followed it.
    assert (placed.outcome, placed.placed_by, placed.reason) == (
        Outcome.PROPOSED,
        "thread",
        "why",
    )
    assert script.requests == []

    # Weeks later the same thread may carry a new order: a model reads it again.
    script = model(answer("link", project=project.number))
    late = mail(
        "<late@x>",
        date="Sun, 20 Sep 2026 09:00:00 +0300",
        references="<order@x>",
        body=QUOTE_ONLY,
    )
    await arrive(company_id, 3, late, category=None)
    assert await service.place_pending() == 1

    assert len(script.requests) == 1 and project.number in script.prompt()
    # A reply of nothing but quoted history shows the model no text its writer did not type.
    assert "[mail 3] 2026-09-20" in script.prompt()
    assert "the quoted history" not in script.prompt()
    assert len(await links(company_id, "<order@x>")) == 3
    # Left in its project, it keeps what was read of the job when the project was proposed.
    placed = await placement(company_id, "<order@x>")
    assert placed.outcome is Outcome.LINKED
    assert placed.reading == {"origin": "Istanbul", "cargo": "steel coils"}
    assert (await placement(company_id, "<order@x>")).placed_by == "model:linker"


@pytest.mark.integration
async def test_an_old_thread_answered_with_a_new_order_sends_only_the_new_mail_to_the_new_project(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    late = mail("<late@x>", date="Sun, 20 Sep 2026 09:00:00 +0300", references="<order@x>")
    await arrive(company_id, 2, late, category=None)
    model(answer("new_project", new_project={}))

    assert await service.place_pending() == 1

    first, second = await the_projects(company_id)
    old, new = await links(company_id, "<order@x>")
    assert (old.project_id, new.project_id) == (first.id, second.id)


@pytest.mark.integration
async def test_a_copy_of_a_mail_from_a_second_mailbox_is_not_read_again(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    script = model(answer("unplaced"))
    assert await service.place_pending() == 1
    # The same mail as another mailbox received it: other delivery headers, the same words.
    copy = mail("<order@x>").replace(b"From:", b"Delivered-To: other@acme.example\nFrom:", 1)
    await arrive(company_id, 2, copy, category=None)

    assert await service.place_pending() == 0

    assert len(script.requests) == 1
    assert not (await placement(company_id, "<order@x>")).waiting


@pytest.mark.integration
async def test_older_mail_that_arrives_late_is_not_swept_into_a_recent_project(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    await arrive(company_id, 2, mail("<bank@x>", subject="Bank"), category=ThreadCategory.AUTOMATED)
    project = await owner_project(company_id, "<order@x>")
    await owner_project(company_id, "<bank@x>", "Another job")
    model()
    assert await service.place_pending() == 2
    # The history walk stores a year-old mail of each thread after the recent ones.
    old = "Mon, 04 Aug 2025 09:00:00 +0300"
    await arrive(company_id, 3, mail("<old@x>", date=old, references="<order@x>"), category=None)
    await arrive(company_id, 4, mail("<notice@x>", date=old, references="<bank@x>"), category=None)
    script = model(answer("unplaced"))

    assert await service.place_pending() == 2

    # The newest mail of the thread did not change, yet the old one is far from every tied mail:
    # a model reads the work conversation, and the system's notice stays unplaced.
    assert len(script.requests) == 1 and project.number in script.prompt()
    assert len(await links(company_id, "<order@x>")) == 1
    notice = await placement(company_id, "<bank@x>")
    assert (notice.outcome, notice.placed_by) == (Outcome.UNPLACED, "keys")
    assert len(await links(company_id, "<bank@x>")) == 1


@pytest.mark.integration
async def test_a_file_read_after_its_mail_was_placed_sends_the_conversation_back(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    model()
    cmr = pdfs.pdf(pdfs.text("International consignment note, CMR No: 778899"))
    first = mail("<cmr@x>", subject="CMR", body="The signed note.", attach={"cmr.pdf": cmr})
    await arrive(company_id, 1, first, category=ThreadCategory.WORK)
    project = await owner_project(company_id, "<cmr@x>")
    # The same file forwarded by someone else, and another file naming the same note: neither
    # mail says the number itself, and neither file has been read yet.
    await arrive(
        company_id,
        2,
        mail("<forward@x>", subject="Fwd", body="See attached.", attach={"scan.pdf": cmr}),
        category=ThreadCategory.AUTOMATED,
    )
    other = pdfs.pdf(pdfs.text("Delivery for CMR No: 778899"))
    await arrive(
        company_id,
        3,
        mail("<pod@x>", subject="POD", body="Delivered.", attach={"pod.pdf": other}),
        category=ThreadCategory.AUTOMATED,
    )
    assert await service.place_pending() == 3
    assert (await placement(company_id, "<pod@x>")).outcome is Outcome.UNPLACED

    assert await documents.read_pending() == 2
    assert await service.place_pending() == 3

    # One file is stored once: the forward is tied through the mail that first carried it.
    [forwarded] = await links(company_id, "<forward@x>")
    [delivered] = await links(company_id, "<pod@x>")
    assert forwarded.project_id == delivered.project_id == project.id
    assert forwarded.method is delivered.method is LinkMethod.KEY
    assert len(delivered.evidence) == 1


@pytest.mark.integration
async def test_a_mail_the_owner_ties_while_the_model_reads_is_left_where_the_owner_put_it(
    client: httpx.AsyncClient, model: Callable[..., Script], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await owner_project(company_id, "<order@x>", "The owner's own")
        proposal = {"decision": "new_project", "reason": "why", "confidence": 0.8}
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args=proposal | {"new_project": {}},
                )
            ]
        )

    model()
    stand_in = FunctionModel(respond)
    monkeypatch.setattr(llm, "model_for", lambda name: stand_in)

    assert await service.place_pending() == 1

    [link] = await links(company_id, "<order@x>")
    assert (link.status, link.method) == (LinkStatus.CONFIRMED, LinkMethod.OWNER)


@pytest.mark.integration
async def test_a_run_that_fails_midway_still_counts_every_request_it_made(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    down = ModelHTTPError(503, "the-model", {"error": {"message": "overloaded"}})
    model(tool("search_projects", words="Stone"), tool("search_mail", words="Munich"), down)

    assert await service.place_pending() == 0

    assert await reads(company_id) == 3
    assert (await placement(company_id, "<order@x>")).failures == 1


@pytest.mark.integration
async def test_a_link_the_owner_rejected_is_never_made_again(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)
    async with open_session() as db:
        await db.execute(
            update(ProjectLink)
            .where(ProjectLink.company_id == company_id)
            .values(status=LinkStatus.REJECTED, decided_at=utcnow())
        )
        await db.commit()

    script = model(answer("link", project=project.number), answer("unplaced"))
    late = mail("<late@x>", date="Sun, 20 Sep 2026 09:00:00 +0300", references="<order@x>")
    await arrive(company_id, 2, late, category=None)
    assert await service.place_pending() == 1

    # The model was told, and its answer naming the project anyway was sent back to it.
    assert "does not belong to" in script.prompt() and project.number in script.prompt()
    assert len(script.requests) == 2
    [rejected] = await links(company_id, "<order@x>")
    assert rejected.status is LinkStatus.REJECTED
    assert (await placement(company_id, "<order@x>")).outcome is Outcome.UNPLACED


@pytest.mark.integration
async def test_two_projects_that_fit_are_put_to_the_owner(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<a@x>", subject="Munich A"), category=ThreadCategory.WORK)
    await arrive(company_id, 2, mail("<b@x>", subject="Munich B"), category=ThreadCategory.WORK)
    first = await owner_project(company_id, "<a@x>", "Munich A")
    second = await owner_project(company_id, "<b@x>", "Munich B")
    await arrive(company_id, 3, mail("<which@x>", subject="POD"), category=ThreadCategory.WORK)
    model(
        tool("search_projects", words="Munich"),
        answer("ask", alternatives=[first.number, second.number]),
    )

    assert await service.place_pending() == 3

    placed = await placement(company_id, "<which@x>")
    assert placed.outcome is Outcome.ASKED and placed.project_id is None
    assert set(placed.alternatives) == {first.id, second.id}
    assert await links(company_id, "<which@x>") == []


@pytest.mark.integration
async def test_a_link_with_another_project_in_sight_is_decided_by_the_second_model(
    client: httpx.AsyncClient, model: Callable[..., Script], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<a@x>", subject="Munich A"), category=ThreadCategory.WORK)
    await arrive(company_id, 2, mail("<b@x>", subject="Munich B"), category=ThreadCategory.WORK)
    first = await owner_project(company_id, "<a@x>", "Munich A")
    second = await owner_project(company_id, "<b@x>", "Munich B")
    for uid, key in ((3, "<pod@x>"), (4, "<cmr@x>"), (5, "<late@x>"), (6, "<down@x>")):
        await arrive(company_id, uid, mail(key, subject="POD"), category=ThreadCategory.WORK)
    look = tool("search_projects", words="Munich")
    look_at = tool("open_project", number=second.number)
    stronger = Script(
        look,
        answer("link", project=second.number),
        UnexpectedModelBehavior("no answer"),
        ModelHTTPError(503, "the-second-model"),
    )
    monkeypatch.setattr(llm, "second_model_for", lambda name: FunctionModel(stronger.respond))
    monkeypatch.setitem(get_settings().agent_second_models, "linker", "openai:the-second")
    script = model(
        # Two projects of the same client were in sight: the same client's other job.
        look,
        answer("link", project=first.number),
        # Only the one it names was: nothing to tell apart.
        look_at,
        answer("link", project=second.number),
        # Two in sight again, and the second model gives no answer, then cannot be reached.
        look,
        answer("link", project=first.number),
        look,
        answer("link", project=first.number),
    )

    assert await service.place_pending() == 6

    # The second model read it from the start, without the first answer, and decided.
    assert (await placement(company_id, "<pod@x>")).project_id == second.id
    assert "why" not in stronger.prompt() and "POD" in stronger.prompt()
    assert (await placement(company_id, "<cmr@x>")).project_id == second.id
    # No answer from it, or none to be had: the first one stands, and is not held back.
    assert (await placement(company_id, "<late@x>")).project_id == first.id
    assert (await placement(company_id, "<down@x>")).project_id == first.id
    assert script.steps == [] and stronger.steps == []
    # Every request either model answered was sent the mail, and counts.
    assert await reads(company_id) == 10


@pytest.mark.integration
async def test_an_answer_that_cannot_be_used_leaves_it_unplaced_and_a_failure_is_tried_again(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    wrong = ("answer", {"decision": "somewhere"})
    model(wrong, wrong, wrong)

    assert await service.place_pending() == 1

    placed = await placement(company_id, "<order@x>")
    assert (placed.outcome, placed.placed_by, placed.waiting) == (
        Outcome.UNPLACED,
        "unanswered",
        False,
    )
    # Asking again would pay again for the same: every request made is counted, and it rests.
    assert await reads(company_id) == 3

    late = mail("<late@x>", date="Sun, 20 Sep 2026 09:00:00 +0300", references="<order@x>")
    await arrive(company_id, 2, late, category=None)
    down = ModelHTTPError(503, "the-model", {"error": {"message": "overloaded"}})
    model(down, down, down, down)
    for _ in range(service.PLACE_TRIES + 1):
        assert await service.place_pending() == 0

    waits = await placement(company_id, "<order@x>")
    # After three tries it waits an hour; the provider being down costs a delay, not the mail.
    assert waits.waiting and waits.failures == service.PLACE_TRIES
    async with open_session() as db:
        await db.execute(
            update(Placement)
            .where(Placement.company_id == company_id)
            .values(failed_at=utcnow() - service.PLACE_RETRY_AFTER - timedelta(minutes=1))
        )
        await db.commit()
    model(answer("unplaced"))
    assert await service.place_pending() == 1


@pytest.mark.integration
async def test_a_change_noted_twice_is_read_once(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    script = model(answer("unplaced"))
    assert await service.place_pending() == 1

    # The look goes back over the last minutes each time, and finds the same change again.
    await service.note_changes(company_id)
    assert await service.place_pending() == 0

    assert len(script.requests) == 1
    async with open_session() as db:
        assert await repository.latest_seen(db, company_id) is not None


@pytest.mark.integration
async def test_a_project_is_named_by_its_job_and_follows_what_later_mail_says(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(
        answer(
            "new_project",
            new_project={
                "client": "Stone  GmbH",
                "origin": "Istanbul, Türkiye",
                "destination": "Munich, Germany",
                "trucks": 3,
                "starts_on": "2026-08-10",
            },
            party="Stone GmbH",
            party_role="client",
        )
    )
    assert await service.place_pending() == 1

    [project] = await the_projects(company_id)
    # Named the same way every time, by code: the client, the towns, the first day.
    assert project.name == "Stone GmbH · Istanbul → Munich · 10.08.2026"
    assert (project.starts_on, project.ends_on) == (date(2026, 8, 10), None)
    assert project.field_sources["starts_on"] == FieldSource.SUGGESTION
    placed = await placement(company_id, "<order@x>")
    assert placed.reading is not None and placed.reading["trucks"] == "3"

    # The carrier's own conversation adds who carries it, an earlier first day and the last one.
    await arrive(
        company_id,
        2,
        mail("<truck@x>", subject="Truck for Munich", body="Our truck loads on the 8th."),
        category=ThreadCategory.WORK,
    )
    model(
        tool("open_project", number=project.number),
        answer(
            "link",
            project=project.number,
            job={"subcontractor": "Hauler Ltd", "starts_on": "2026-08-08", "ends_on": "2026-08-14"},
            party="Hauler Ltd",
            party_role="subcontractor",
        ),
    )
    assert await service.place_pending() == 1

    # A first day moved renames it, by the titler or, without one, by code.
    [project] = await name_projects(company_id, model)
    assert project.name == "Stone GmbH · Istanbul → Munich · 08.08.2026"
    assert (project.starts_on, project.ends_on) == (date(2026, 8, 8), date(2026, 8, 14))

    # The owner's own name and first day stand: later mail proposes a day, and renames nothing.
    assert (
        await client.patch(
            f"/projects/{project.id}", json={"name": "Stone, August", "starts_on": "2026-08-09"}
        )
    ).status_code == 200
    await arrive(
        company_id,
        3,
        mail("<later@x>", subject="Loading moved", body="Loading is now on the 7th."),
        category=ThreadCategory.WORK,
    )
    model(
        tool("open_project", number=project.number),
        answer("link", project=project.number, job={"starts_on": "2026-08-07"}),
    )
    assert await service.place_pending() == 1

    [project] = await the_projects(company_id)
    assert (project.name, project.starts_on) == ("Stone, August", date(2026, 8, 9))
    proposals = (await client.get("/field-proposals", params={"status": "open"})).json()["items"]
    assert [(p["field"], p["value"]) for p in proposals] == [("starts_on", "2026-08-07")]


@pytest.mark.integration
async def test_a_job_with_neither_client_nor_route_is_named_by_who_wrote_and_the_day(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(answer("new_project", new_project={"destination": "Iraq"}))
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)
    # Never the model's words: the sender's name and the day of the first mail.
    assert project.name == "Ada Stone · 03.08.2026"

    # Once a conversation names the client, the project is named by it and where it goes.
    await arrive(
        company_id,
        2,
        mail("<client@x>", subject="Our load to Iraq", body="Please confirm our load."),
        category=ThreadCategory.WORK,
    )
    model(
        tool("open_project", number=project.number),
        answer("link", project=project.number, job={"client": "Stone GmbH"}),
    )
    assert await service.place_pending() == 1
    [project] = await name_projects(company_id, model)
    assert project.name == "Stone GmbH · → Iraq"


@pytest.mark.integration
async def test_a_mail_address_or_a_domain_is_no_firms_name(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    # All the mail shows of who wrote is where it came from, and the model gives that.
    model(
        answer(
            "new_project",
            party="ada@stone.example",
            party_role="client",
            new_project={
                "client": "stone.example",
                "consignee": "Weber Logistik GmbH",
            },
        )
    )
    assert await service.place_pending() == 1

    # The project is named by who wrote and the day, not by a domain, and no firm is kept that
    # is only an address; a firm named by its name stays.
    [project] = await the_projects(company_id)
    assert project.name == "Ada Stone · 03.08.2026"
    placed = await placement(company_id, "<order@x>")
    assert placed.reading == {"consignee": "Weber Logistik GmbH"}


@pytest.mark.integration
async def test_a_conversation_the_company_is_only_copied_on_starts_no_project(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    # The mailbox read is the company's: its domain tells who a mail was written to.
    async with open_session() as db:
        db.add(
            MailFolder(
                company_id=company_id, mailbox=OWN, folder="INBOX", uidvalidity=1, last_uid=0
            )
        )
        await db.commit()
    shipped = mail(
        "<ship@x>",
        subject="Three trucks onto the ship",
        body="Please take our three trucks onto Friday's ship.",
        to="Port Desk <desk@port.example>",
        cc=OWN,
    )
    await arrive(company_id, 1, shipped, category=ThreadCategory.WORK)
    script = model(NEW_PROJECT)
    assert await service.place_pending() == 1

    # Other firms arranging their own work: the model is told so, and whatever it answers, no
    # project starts.
    assert "From: Ada Stone <ada@stone.example> (the company only copied)" in script.prompt()
    assert await the_projects(company_id) == []
    assert (await placement(company_id, "<ship@x>")).outcome is Outcome.UNPLACED

    # A later mail of it written to the company can start one.
    asked = mail(
        "<ask@x>",
        subject="Re: Three trucks onto the ship",
        body="Could you carry them to the port on Thursday?",
        date="Tue, 04 Aug 2026 09:00:00 +0300",
        references="<ship@x>",
        to=OWN,
    )
    await arrive(company_id, 2, asked, category=None)
    script = model(NEW_PROJECT)
    assert await service.place_pending() == 1
    assert "(to the company)" in script.prompt()
    assert [p.status for p in await the_projects(company_id)] == [ProjectStatus.PROPOSED]


@pytest.mark.integration
async def test_the_titler_names_a_project_and_never_over_the_owners_name(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    job = {"client": "Stone GmbH", "origin": "Istanbul", "destination": "Munich"}
    model(answer("new_project", new_project=job | {"cargo": "steel coils"}))
    assert await service.place_pending() == 1
    async with open_session() as db:
        queued = await db.scalar(
            sql(
                "SELECT count(*) FROM procrastinate_jobs"
                " WHERE task_name = 'linking.name_project' AND status = 'todo'"
            )
        )
        left = await conversations.model_reads_left(db, company_id)
    assert queued == 1

    # Its words, cleaned of what a model wraps them in, from the day's budget.
    [project] = await name_projects(company_id, model, '"Stone · Istanbul → Munich · coils."')
    assert project.name == "Stone · Istanbul → Munich · coils"
    async with open_session() as db:
        assert await conversations.model_reads_left(db, company_id) == left - 1

    # A name the owner gave is never asked for again.
    renamed = await client.patch(f"/projects/{project.id}", json={"name": "Stone coils"})
    assert renamed.status_code == 200
    script = model(text("Something else"))
    await service.name_project(company_id, project.id)
    assert script.requests == []
    assert [p.name for p in await the_projects(company_id)] == ["Stone coils"]


@pytest.mark.integration
@pytest.mark.parametrize("address", ["stone.example", "ada@stone.example", "www.taşımacı.com.tr"])
async def test_a_project_is_not_proposed_under_an_address_alone(
    client: httpx.AsyncClient, model: Callable[..., Script], address: str
) -> None:
    company_id = await guest_client(client)
    await arrive(
        company_id, 1, mail("<order@x>", sender="ada@stone.example"), category=ThreadCategory.WORK
    )
    # Nothing but an address tells who wrote, and the model names the client by it.
    model(answer("new_project", new_project={"client": address}, party=address))
    assert await service.place_pending() == 1

    assert await the_projects(company_id) == []
    assert (await placement(company_id, "<order@x>")).outcome is Outcome.UNPLACED


@pytest.mark.integration
async def test_a_conversation_the_owner_rejects_no_longer_names_or_dates_the_project(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    job = {"client": "Stone GmbH", "origin": "Istanbul", "destination": "Munich"}
    model(answer("new_project", new_project=job | {"starts_on": "2026-08-10"}))
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)
    await arrive(
        company_id,
        2,
        mail("<truck@x>", subject="Truck for Munich", body="Our truck loads on the 8th."),
        category=ThreadCategory.WORK,
    )
    model(
        tool("open_project", number=project.number),
        answer(
            "link",
            project=project.number,
            job={"starts_on": "2026-08-08", "ends_on": "2026-08-14"},
        ),
    )
    assert await service.place_pending() == 1
    [project] = await name_projects(company_id, model)
    assert project.name == "Stone GmbH · Istanbul → Munich · 08.08.2026"

    # The owner says that conversation is not of this job: what it said leaves the project.
    wrong = await placement(company_id, "<truck@x>")
    answered = await client.patch(f"/placements/{wrong.id}", json={"links": "rejected"})
    assert answered.status_code == 200
    [project] = await name_projects(company_id, model)
    assert project.name == "Stone GmbH · Istanbul → Munich · 10.08.2026"
    assert (project.starts_on, project.ends_on) == (date(2026, 8, 10), None)


@pytest.mark.integration
async def test_a_project_shows_its_job_as_its_conversations_say_it_together(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    model(
        answer(
            "new_project",
            new_project={
                "client": "Stone GmbH",
                "subcontractor": "Hauler Transport",
                "origin": "Istanbul",
                "destination": "Munich",
                "trucks": 2,
            },
        )
    )
    assert await service.place_pending() == 1
    [project] = await the_projects(company_id)

    # The carrier's own conversation: it reads as the client, the firm the mail deals with as
    # the subcontractor. Most conversations call it the subcontractor, so it is that alone.
    await arrive(
        company_id,
        2,
        mail("<truck@x>", subject="Truck for Munich", body="Our truck loads on the 8th."),
        category=ThreadCategory.WORK,
    )
    model(
        tool("open_project", number=project.number),
        answer(
            "link",
            project=project.number,
            job={"client": "Hauler Transport"},
            party="Hauler Transport",
            party_role="subcontractor",
        ),
    )
    assert await service.place_pending() == 1

    shown = (await client.get(f"/projects/{project.id}")).json()
    assert shown["name"] == "Stone GmbH · Istanbul → Munich"
    assert shown["job"] == {
        "client": "Stone GmbH",
        "subcontractors": ["Hauler Transport"],
        "consignee": None,
        "origin": "Istanbul",
        "destination": "Munich",
        "cargo": None,
        "trucks": 2,
        # No card in the books is named as either: both are named in mail only.
        "not_in_books": ["Stone GmbH", "Hauler Transport"],
    }
    listed = (await client.get("/projects")).json()["items"]
    assert [item["job"] for item in listed] == [shown["job"]]


@pytest.mark.integration
async def test_a_reply_naming_a_truck_its_job_has_not_had_is_read_as_maybe_the_next_load(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    order = mail("<order@x>", body="We confirm the load for Monday. Truck: AB 123 CD")
    await arrive(company_id, 1, order, category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    await service.place_pending()
    [project] = await the_projects(company_id)
    # The same truck, two days on: the same job, followed without a model.
    script = model()
    same = mail(
        "<same@x>",
        date="Wed, 05 Aug 2026 09:00:00 +0300",
        references="<order@x>",
        body="Truck: AB 123 CD is on its way.",
    )
    await arrive(company_id, 2, same, category=None)
    assert await service.place_pending() == 1
    assert script.requests == []

    # Another truck in the same thread three days on: the next load, which a model reads.
    script = model(answer("new_project", new_project={}, cited_mails=[3]))
    other = mail(
        "<next@x>",
        date="Sat, 08 Aug 2026 09:00:00 +0300",
        references="<order@x>",
        body="Next one: Truck: XY 987 ZW loads Monday.",
    )
    await arrive(company_id, 3, other, category=None)
    assert await service.place_pending() == 1

    assert "plate XY 987 ZW" in script.prompt() and "next load" in script.prompt()
    first, second, third = await links(company_id, "<order@x>")
    assert first.project_id == second.project_id == project.id
    assert third.project_id != project.id
    # The first project keeps what its mails said of the job.
    job = (await client.get(f"/projects/{project.id}")).json()["job"]
    assert (job["origin"], job["cargo"]) == ("Istanbul", "steel coils")

    # A reply on the next load follows it, the conversation's last project, without a model.
    script = model()
    loaded = mail(
        "<loaded@x>",
        date="Sun, 09 Aug 2026 09:00:00 +0300",
        references="<order@x>",
        body="Truck: XY 987 ZW is loaded.",
    )
    await arrive(company_id, 4, loaded, category=None)
    assert await service.place_pending() == 1
    assert script.requests == []
    assert (await links(company_id, "<order@x>"))[3].project_id == third.project_id


@pytest.mark.integration
async def test_a_reply_waits_for_its_files_before_it_follows(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    order = mail("<order@x>", body="We confirm the load for Monday. Truck: AB 123 CD")
    await arrive(company_id, 1, order, category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    await service.place_pending()
    note = pdfs.pdf(pdfs.text("CMR consignment note. Truck: XY 987 ZW"))
    reply = mail(
        "<cmr@x>",
        date="Wed, 05 Aug 2026 09:00:00 +0300",
        references="<order@x>",
        body="The note attached.",
        attach={"CMR.pdf": note},
    )
    await arrive(company_id, 2, reply, category=None)

    # Its file is not read yet: it waits rather than follow.
    script = model()
    assert await service.place_pending() == 0
    assert len(await links(company_id, "<order@x>")) == 1

    # Read, the file names another truck: a model reads the reply.
    await documents.read_pending()
    script = model(answer("new_project", new_project={}, cited_mails=[2]))
    assert await service.place_pending() == 1
    assert "plate XY 987 ZW" in script.prompt()


@pytest.mark.integration
async def test_a_reply_whose_file_stays_unread_is_placed_once_the_wait_is_over(
    client: httpx.AsyncClient, model: Callable[..., Script], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    order = mail("<order@x>", body="We confirm the load for Monday. Truck: AB 123 CD")
    await arrive(company_id, 1, order, category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    await service.place_pending()
    reply = mail(
        "<cmr@x>",
        date="Wed, 05 Aug 2026 09:00:00 +0300",
        references="<order@x>",
        body="The note attached.",
        attach={"CMR.pdf": pdfs.pdf(pdfs.text("CMR consignment note."))},
    )
    await arrive(company_id, 2, reply, category=None)
    script = model()
    assert await service.place_pending() == 0

    # Nothing reads the file, and nothing changes in the conversation: a day on, it follows.
    later = utcnow() + service.FILES_WAIT + timedelta(minutes=1)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert await service.place_pending() == 1
    assert script.requests == []
    assert len(await links(company_id, "<order@x>")) == 2


@pytest.mark.integration
async def test_a_split_conversation_keeps_its_place_among_the_project_readings(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    early = mail("<early@x>", subject="Izmir load", body="Steel coils, truck AB 123 CD.")
    await arrive(company_id, 1, early, category=ThreadCategory.WORK)
    model(answer("new_project", new_project={"origin": "Izmir"}))
    await service.place_pending()
    [project] = await the_projects(company_id)
    order = mail("<order@x>", body="We confirm the load for Monday. Truck: AB 123 CD")
    await arrive(company_id, 2, order, category=ThreadCategory.WORK)
    model(
        tool("open_project", number=project.number),
        answer("link", project=project.number, job={"origin": "Istanbul"}),
    )
    assert await service.place_pending() == 1
    # The two conversations say two origins: the one noted last names it.
    assert (await client.get(f"/projects/{project.id}")).json()["job"]["origin"] == "Istanbul"

    model(answer("new_project", new_project={}, cited_mails=[2]))
    other = mail(
        "<next@x>",
        date="Sat, 08 Aug 2026 09:00:00 +0300",
        references="<order@x>",
        body="Next one: Truck: XY 987 ZW loads Monday.",
    )
    await arrive(company_id, 3, other, category=None)
    assert await service.place_pending() == 1

    # Its later mails went to the next load; what it read before keeps its place in the order.
    assert len(await the_projects(company_id)) == 2
    assert (await client.get(f"/projects/{project.id}")).json()["job"]["origin"] == "Istanbul"


@pytest.mark.integration
async def test_a_merge_keeps_what_either_project_kept_of_one_conversation(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"), category=ThreadCategory.WORK)
    first = await owner_project(company_id, "<order@x>", name="First")
    second = await owner_project(company_id, "<order@x>", name="Second")
    await service.note_changes(company_id)
    async with open_session() as db:
        thread_id = await db.scalar(
            select(Thread.id).where(
                Thread.company_id == company_id, Thread.thread_key == "<order@x>"
            )
        )
        assert thread_id is not None
        await repository.keep_reading(db, company_id, thread_id, first.id, {"origin": "Istanbul"})
        await repository.keep_reading(
            db, company_id, thread_id, second.id, {"origin": "Izmir", "cargo": "steel coils"}
        )
        await repository.move_kept_readings(db, company_id, second.id, first.id)
        await db.commit()
        kept = await repository.kept_readings(db, company_id, [first.id, second.id])

    # The merged project's own row goes when the project does.
    assert {row.project_id: row.reading for row in kept} == {
        first.id: {"origin": "Istanbul", "cargo": "steel coils"},
        second.id: {"origin": "Izmir", "cargo": "steel coils"},
    }


@pytest.mark.integration
async def test_a_number_quoted_over_more_than_a_month_names_no_one_job(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    blanket = "Under our standing order no 4500123."
    await arrive(
        company_id,
        1,
        mail("<july@x>", subject="July load", body=blanket, date="Wed, 01 Jul 2026 09:00:00 +0300"),
        category=ThreadCategory.WORK,
    )
    await owner_project(company_id, "<july@x>", name="July")
    script = model(answer("unplaced"))

    august = mail(
        "<aug@x>", subject="August load", body=blanket, date="Sat, 15 Aug 2026 09:00:00 +0300"
    )
    await arrive(company_id, 2, august, category=ThreadCategory.WORK)
    await service.place_pending()

    # The order is on mail six weeks apart: a model reads it rather than code tying it by number.
    assert len(script.requests) == 1
    assert await links(company_id, "<aug@x>") == []


async def placed_by_an_earlier_rule(company_id: uuid.UUID, model: Callable[..., Script]) -> Project:
    """A conversation whose replies all followed its first project, as an earlier rule placed
    them: the same truck two days on, another truck three days later, and that truck loaded."""
    order = mail("<order@x>", body="We confirm the load for Monday. Truck: AB 123 CD")
    await arrive(company_id, 1, order, category=ThreadCategory.WORK)
    model(NEW_PROJECT)
    await service.place_pending()
    [project] = await the_projects(company_id)
    replies = {
        "<same@x>": ("Wed, 05 Aug 2026 09:00:00 +0300", "Truck: AB 123 CD is on its way."),
        "<next@x>": ("Sat, 08 Aug 2026 09:00:00 +0300", "Next one: Truck: XY 987 ZW loads Monday."),
        "<loaded@x>": ("Sun, 09 Aug 2026 09:00:00 +0300", "Truck: XY 987 ZW is loaded."),
    }
    for uid, (key, (sent, body)) in enumerate(replies.items(), start=2):
        reply = mail(key, date=sent, references="<order@x>", body=body)
        await arrive(company_id, uid, reply, category=None)
    async with open_session() as db:
        mail_ids = await db.scalars(
            select(Message.id).where(
                Message.company_id == company_id, Message.rfc_message_id.in_(list(replies))
            )
        )
        for mail_id in mail_ids.all():
            await projects.link_item(
                db,
                company_id=company_id,
                item_kind=ItemKind.MESSAGE,
                item_id=mail_id,
                project_id=project.id,
                method=LinkMethod.SCORED,
                confidence=0.6,
            )
        await db.commit()
    return project


@pytest.mark.integration
async def test_a_thread_that_carried_the_next_load_is_placed_again_in_the_order_it_came(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    project = await placed_by_an_earlier_rule(company_id, model)

    counted = await service.recut_threads(company_id, write=False)
    assert (counted.threads, counted.mails, counted.news) == (1, 3, 1)
    assert [link.project_id for link in await links(company_id, "<order@x>")] == [project.id] * 4

    script = model(answer("new_project", new_project={}, cited_mails=[3]))
    done = await service.recut_threads(company_id, write=True, max_reads=5)

    # The same truck followed by code; the other truck was read, and is the next load, which
    # the reply after it follows by code.
    assert (done.placed, done.reads, done.failed) == (3, 1, 0)
    assert len(script.requests) == 1
    first, second, third, fourth = await links(company_id, "<order@x>")
    assert first.project_id == second.project_id == project.id
    assert third.project_id != project.id and fourth.project_id == third.project_id
    assert not (await placement(company_id, "<order@x>")).waiting


@pytest.mark.integration
async def test_a_mail_that_fails_to_be_placed_again_waits_for_the_worker(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    project = await placed_by_an_earlier_rule(company_id, model)

    model(ModelHTTPError(503, "the-model"))
    done = await service.recut_threads(company_id, write=True, max_reads=5)

    # The model could not be reached for the other truck: it, and the reply after it, wait for
    # the worker rather than be placed together by the next replay.
    assert (done.placed, done.failed) == (1, 1)
    assert [link.project_id for link in await links(company_id, "<order@x>")] == [project.id] * 2
    assert (await placement(company_id, "<order@x>")).waiting

    model(answer("new_project", new_project={}, cited_mails=[3, 4]))
    assert await service.place_pending() == 1
    first, second, third, fourth = await links(company_id, "<order@x>")
    assert first.project_id == second.project_id == project.id
    assert third.project_id == fourth.project_id != project.id


@pytest.mark.integration
async def test_no_more_mail_is_read_again_than_the_reads_given(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    await placed_by_an_earlier_rule(company_id, model)

    script = model()
    done = await service.recut_threads(company_id, write=True, max_reads=0)

    assert (done.placed, done.reads) == (1, 0) and script.requests == []
    assert (await placement(company_id, "<order@x>")).waiting


@pytest.mark.integration
async def test_a_conversation_in_a_project_the_owner_rejected_is_not_placed_again(
    client: httpx.AsyncClient, model: Callable[..., Script]
) -> None:
    company_id = await guest_client(client)
    project = await placed_by_an_earlier_rule(company_id, model)
    rejected = await client.patch(f"/projects/{project.id}", json={"status": "rejected"})
    assert rejected.status_code == 200

    counted = await service.recut_threads(company_id, write=False)

    assert (counted.threads, counted.mails) == (0, 0)


def test_a_conversation_follows_its_last_project_only_after_it_began() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    mails = [uuid.uuid4() for _ in range(4)]
    thread = cast(ThreadRead, SimpleNamespace(mails=[SimpleNamespace(id=m) for m in mails]))

    # The next load's mails run on to the end: a new one follows it.
    assert service._latest(thread, {a: {mails[0], mails[1]}, b: {mails[2]}}) == b
    # They go back and forth: none.
    assert service._latest(thread, {a: {mails[0], mails[2]}, b: {mails[1], mails[3]}}) is None
    # An untied mail older than the next load's first may be the earlier job's: none.
    assert service._latest(thread, {a: {mails[0]}, b: {mails[2], mails[3]}}) is None
    # In one project alone, an older mail follows it as before.
    assert service._latest(thread, {a: {mails[2], mails[3]}}) == a
