import email.message
import uuid
from collections.abc import Callable

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.messages import UserPromptPart as UserPrompt
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select, update

from app.config import get_settings
from app.modules.conversations import repository, service
from app.modules.conversations.adapters.imap import FetchedMail
from app.modules.conversations.mail import parse_mail
from app.modules.conversations.models import Message, Thread
from app.platform import llm
from app.platform.clock import utcnow
from app.platform.db import open_session
from app.tests.conftest import guest_client

OWN = "ops@acme.example"
CUSTOMER = "Ada Stone <ada@stone.example>"
NEWSLETTER = {"List-Unsubscribe": "<https://news.example/unsubscribe>"}

type Answer = Callable[[], dict[str, object] | Exception]


def mail(
    message_id: str,
    *,
    sender: str = CUSTOMER,
    subject: str = "Munich load",
    body: str = "Can you load Monday? Order no 4500123.",
    references: str | None = None,
    headers: dict[str, str] | None = None,
    files: tuple[str, ...] = (),
) -> bytes:
    message = email.message.EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message["Date"] = "Mon, 28 Sep 2026 09:00:00 +0300"
    message["Message-ID"] = message_id
    if references:
        message["References"] = references
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(body)
    for name in files:
        message.add_attachment(b"%PDF-1.7 a", maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()


async def arrive(company_id: uuid.UUID, uid: int, raw: bytes, *, sent: bool = False) -> None:
    """Stores a mail as seen in the inbox, or in the Sent folder, and processes what waits."""
    folder = "Sent" if sent else "INBOX"
    async with open_session() as db:
        await service.ingest_mail(
            db,
            company_id=company_id,
            place=service.FolderPlace(mailbox=OWN, folder=folder, uidvalidity=1, sent=sent),
            fetched=FetchedMail(uid=uid, raw=raw, internaldate=None),
            own_addresses={OWN},
        )
        await db.commit()
    await service.process_pending()


async def sorting(company_id: uuid.UUID, thread_key: str) -> tuple[str | None, str | None]:
    async with open_session() as db:
        thread = await repository.get_thread(db, company_id, thread_key)
    assert thread is not None
    return thread.category, thread.sorted_by


async def reads(company_id: uuid.UUID) -> int:
    """Model reads counted against the company's budget for the last day."""
    async with open_session() as db:
        return await repository.model_reads_since(db, company_id, utcnow() - service.SORT_WINDOW)


class Asked:
    """What a stand-in for the model was sent, call by call."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.infos: list[AgentInfo] = []

    def prompts(self) -> list[str]:
        return [
            "".join(item for item in part.content if isinstance(item, str))
            for request in self.requests
            for part in request.parts
            if isinstance(part, UserPrompt)
        ]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Callable[[Answer], Asked]:
    """Puts a stand-in in the model's place, with a budget of forty conversations a day."""
    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 40)

    def install(answer: Answer) -> Asked:
        asked = Asked()

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            request = messages[0]
            assert isinstance(request, ModelRequest)
            asked.requests.append(request)
            asked.infos.append(info)
            answered = answer()
            if isinstance(answered, Exception):
                raise answered
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=answered)]
            )

        stand_in = FunctionModel(respond)
        monkeypatch.setattr(llm, "model_for", lambda name: stand_in)
        return asked

    return install


def says(category: str, reason: str = "A customer asks for a load") -> Answer:
    return lambda: {"category": category, "reason": reason}


def test_bulk_mail_is_known_by_what_bulk_senders_add() -> None:
    assert parse_mail(mail("<a@x>", headers=NEWSLETTER)).bulk
    assert parse_mail(mail("<b@x>", headers={"List-Id": "<news.example>"})).bulk
    assert parse_mail(mail("<c@x>", headers={"Precedence": "bulk"})).bulk
    assert parse_mail(mail("<d@x>", headers={"Feedback-ID": "campaign:1"})).bulk
    # A person's mail, and a system telling one customer of one event, carry none of them.
    assert not parse_mail(mail("<e@x>")).bulk
    assert not parse_mail(mail("<f@x>", headers={"Auto-Submitted": "auto-generated"})).bulk


@pytest.mark.integration
async def test_code_sorts_what_the_mail_itself_decides_and_sends_nothing_to_a_model(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 0)
    asked = model(says("work"))
    await arrive(company_id, 1, mail("<news@x>", sender="news@shop.example", headers=NEWSLETTER))
    await arrive(company_id, 2, mail("<quote@x>"))
    await arrive(company_id, 3, mail("<answer@x>", sender=OWN, references="<quote@x>"))
    await arrive(company_id, 4, mail("<order@x>"))

    # The thread the company wrote in was work as soon as its mail was processed.
    assert await sorting(company_id, "<quote@x>") == ("work", "own_mail")
    assert await service.sort_pending() == 1

    assert await sorting(company_id, "<news@x>") == ("noise", "headers")
    # A customer's mail nobody answered needs a model, and there is no budget for one: it waits.
    assert await sorting(company_id, "<order@x>") == (None, None)
    assert asked.requests == []


@pytest.mark.integration
async def test_the_model_sorts_the_rest_from_the_start_of_the_conversation_as_fenced_data(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked]
) -> None:
    company_id = await guest_client(client)
    quoted = "Thanks.\n\nOn Mon, 28 Sep 2026 at 09:00, Ada Stone wrote:\n> the quoted history"
    await arrive(company_id, 1, mail("<order@x>", files=("order 4500123.pdf",)))
    await arrive(company_id, 2, mail("<more@x>", body=quoted, references="<order@x>"))
    asked = model(says("work"))

    assert await service.sort_pending() == 1
    assert await service.sort_pending() == 0

    assert await sorting(company_id, "<order@x>") == ("work", "model:intake")
    async with open_session() as db:
        thread = await repository.get_thread(db, company_id, "<order@x>")
    assert thread is not None and thread.reason == "A customer asks for a load"
    # ADR-0013: one call, no tools, a typed answer, the mail between two lines with one marker.
    [prompt] = asked.prompts()
    assert asked.infos[0].function_tools == [] and not asked.infos[0].allow_text_output
    marker = prompt.split()[3]
    assert prompt.startswith(f"BEGIN OUTSIDE DATA {marker}\n")
    assert prompt.rstrip().endswith(f"END OUTSIDE DATA {marker}")
    # Who wrote, the subject, the files' names and what each writer typed; not what a reply quotes.
    for shown in ("Ada Stone", "Munich load", "order 4500123.pdf", "Order no 4500123", "Thanks."):
        assert shown in prompt
    assert "the quoted history" not in prompt
    assert "Order no 4500123" not in (asked.infos[0].instructions or "")


@pytest.mark.integration
async def test_a_budget_of_model_reads_is_never_passed(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked], monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await guest_client(client)
    monkeypatch.setattr(get_settings(), "mail_model_reads_per_day", 1)
    asked = model(says("automated", "A bank reports a transfer"))
    await arrive(company_id, 1, mail("<first@x>", subject="Transfer received"))
    await arrive(company_id, 2, mail("<second@x>", subject="Transfer sent"))

    assert await service.sort_pending() == 1
    assert await service.sort_pending() == 0

    assert len(asked.requests) == 1
    waiting = {await sorting(company_id, key) for key in ("<first@x>", "<second@x>")}
    assert waiting == {("automated", "model:intake"), (None, None)}

    later = utcnow() + service.SORT_WINDOW
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert await service.sort_pending() == 1
    assert len(asked.requests) == 2


@pytest.mark.integration
async def test_an_answer_that_cannot_be_used_keeps_the_conversation_as_work_and_asks_once(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"))
    asked = model(says("spam"))

    assert await service.sort_pending() == 1
    assert await service.sort_pending() == 0

    # Asked, and asked again for an answer that fits; then kept, since dropping it could lose work.
    assert len(asked.requests) == 2
    assert await sorting(company_id, "<order@x>") == ("work", "unanswered")
    # Both requests were sent the mail, and the budget counts both.
    assert await reads(company_id) == 2


@pytest.mark.integration
async def test_a_model_that_cannot_be_reached_leaves_the_conversation_waiting(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked]
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<order@x>"))
    model(lambda: ModelHTTPError(503, "the-model"))

    assert await service.sort_pending() == 0

    assert await sorting(company_id, "<order@x>") == (None, None)
    async with open_session() as db:
        thread = await repository.get_thread(db, company_id, "<order@x>")
    assert thread is not None and thread.sort_failures == 1
    # The provider may have read the mail before it failed: the try counts against the budget.
    assert await reads(company_id) == 1


@pytest.mark.integration
async def test_later_mail_changes_what_code_decided_but_not_what_a_model_did(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked]
) -> None:
    company_id = await guest_client(client)
    asked = model(says("noise", "A supplier's price list"))
    await arrive(company_id, 1, mail("<news@x>", sender="news@shop.example", headers=NEWSLETTER))
    await arrive(company_id, 2, mail("<prices@x>", subject="Price list"))
    await service.sort_pending()
    assert await sorting(company_id, "<news@x>") == ("noise", "headers")
    assert await sorting(company_id, "<prices@x>") == ("noise", "model:intake")

    # A person answers the newsletter: it is no longer bulk mail only, so it is sorted again.
    await arrive(company_id, 3, mail("<reply@x>", references="<news@x>"))
    assert await sorting(company_id, "<news@x>") == (None, None)
    # More mail from outside in a thread the model sorted asks the model nothing new.
    await arrive(company_id, 4, mail("<prices2@x>", references="<prices@x>"))
    assert await sorting(company_id, "<prices@x>") == ("noise", "model:intake")
    # The company writing in it makes it work, whatever it was.
    await arrive(company_id, 5, mail("<ours@x>", sender=OWN, references="<prices@x>"))
    assert await sorting(company_id, "<prices@x>") == ("work", "own_mail")
    assert len(asked.requests) == 1


@pytest.mark.integration
async def test_a_reply_read_before_its_first_mail_ends_as_one_sorted_conversation(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    in_reply = email.message_from_bytes(mail("<reply@x>"))
    in_reply["In-Reply-To"] = "<first@x>"
    await arrive(company_id, 1, in_reply.as_bytes())
    assert await sorting(company_id, "<first@x>") == (None, None)

    # The first mail arrives second, and names a thread of its own: the reply moves under it.
    await arrive(company_id, 2, mail("<first@x>", sender=OWN, references="<root@x>"))

    assert await sorting(company_id, "<root@x>") == ("work", "own_mail")
    async with open_session() as db:
        keys = (await db.scalars(select(Thread.thread_key))).all()
    assert keys == ["<root@x>"]


@pytest.mark.integration
async def test_a_sorted_reply_keeps_its_sorting_when_its_first_mail_arrives(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked]
) -> None:
    company_id = await guest_client(client)
    asked = model(says("automated", "A bank reports a transfer"))
    in_reply = email.message_from_bytes(mail("<reply@x>"))
    in_reply["In-Reply-To"] = "<first@x>"
    await arrive(company_id, 1, in_reply.as_bytes())
    await service.sort_pending()
    assert await sorting(company_id, "<first@x>") == ("automated", "model:intake")

    await arrive(company_id, 2, mail("<first@x>", references="<root@x>"))

    # What the model decided moved with the mail, and the model is not asked again.
    assert await sorting(company_id, "<root@x>") == ("automated", "model:intake")
    assert await service.sort_pending() == 0
    assert len(asked.requests) == 1


@pytest.mark.integration
async def test_a_mail_found_later_in_the_sent_folder_makes_its_conversation_work(
    client: httpx.AsyncClient, model: Callable[[Answer], Asked]
) -> None:
    company_id = await guest_client(client)
    model(says("noise", "A note to self"))
    # Written by the owner from an address Tamga does not know as theirs, and seen in the inbox.
    raw = mail("<note@x>", sender="deniz@personal.example")
    await arrive(company_id, 1, raw)
    await service.sort_pending()
    assert await sorting(company_id, "<note@x>") == ("noise", "model:intake")

    await arrive(company_id, 2, raw, sent=True)

    assert await sorting(company_id, "<note@x>") == ("work", "own_mail")


@pytest.mark.integration
async def test_mail_stored_before_bulk_was_marked_learns_it_when_read_again(
    client: httpx.AsyncClient,
) -> None:
    company_id = await guest_client(client)
    await arrive(company_id, 1, mail("<news@x>", sender="news@shop.example", headers=NEWSLETTER))
    async with open_session() as db:
        await db.execute(update(Message).values(bulk=False))
        await db.execute(update(Thread).values(category=None, sorted_by=None, sorted_at=None))
        await db.commit()

    async with open_session() as db:
        await service.reread_stored_mail(db, company_id, after=None)
        await db.commit()

    assert await service.sort_pending() == 1
    assert await sorting(company_id, "<news@x>") == ("noise", "headers")
