"""Chats (ADR-0018, ADR-0006): the conversations between a company's people and Tamga, kept on
the server, and the runs of the assistant in them.

A chat belongs to the company, not to one user, and is bound to no subject. Its messages are
stored as the model layer holds them, and a run is always given the stored history: what a
client sends of earlier messages is ignored, so a browser cannot rewrite what was said or what
a tool returned. The chat protocol (AG-UI) is spoken by Pydantic AI's adapter; this module only
decides what a run is given and keeps what it wrote.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Final

from ag_ui.core import BaseEvent, RunAgentInput, RunErrorEvent, UserMessage
from fastapi import Request
from fastapi.responses import Response
from pydantic import ValidationError
from pydantic_ai import UsageLimits
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.usage import RunUsage
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Effort, get_settings
from app.modules.assistant import agents, context, repository
from app.modules.assistant.models import CardKind, Chat, MessageKind, Origin
from app.modules.assistant.schemas import (
    AssistantOut,
    CardContent,
    CardMessage,
    ChatMessages,
    ChatOut,
    ChatPage,
    CompactionMessage,
    RunChoice,
)
from app.modules.assistant.seat import Seat
from app.modules.companies import service as companies
from app.modules.projects import service as projects
from app.platform import events, llm
from app.platform.auth import Actor, Role
from app.platform.clock import utcnow
from app.platform.db import open_session, violated_constraint
from app.platform.errors import BadRequest, Conflict, NotFound, TooManyRequests
from app.platform.pagination import PageParams

logger = logging.getLogger(__name__)

# The budget of requests (CHAT_REQUESTS_PER_DAY) counts those made in this long.
RUN_WINDOW: Final = timedelta(days=1)
# The most requests one answer may take: the searches before it and the answer itself. A run
# that starts within the day's budget finishes, so a day can end this many requests past it.
RUN_REQUESTS: Final = 15
# What a reader is told of a run that failed, as the message and the code of the protocol's
# error event: a client shows its own words for it.
RUN_FAILED: Final = "run_failed"
# The longest message a user may write.
MESSAGE_LENGTH: Final = 20_000
# A chat with no title takes the start of its first message at once, and a moment later the
# name a model gives that message.
_TITLE_LENGTH: Final = 60
# What the model that names a chat reads of its first message: a name needs no more of it.
_TITLE_READS: Final = 1_000
# What a model puts around a name, asked not to.
_TITLE_WRAPPED: Final = " \"'“”‘’«»."
# The model's reasoning is kept with its messages, so its next run has it, and is not sent to
# a client: the protocol carries it as an opaque value for a client to hand back, and no client
# hands this server anything back.
_REASONING: Final = "reasoning"
_REASONING_EVENTS: Final = "REASONING_"
# A run that began this long ago and never ended is given up for dead (its process stopped):
# the chat takes its next one.
RUN_STALE: Final = timedelta(minutes=10)
# The tasks of the runs this process has in flight, by chat: what Stop cancels. Whether a chat
# has a run at all is its row's ``running_since``, which every process reads.
_runs: Final[dict[uuid.UUID, asyncio.Task[None]]] = {}
# The constraint a chat's id breaks when a chat already has it.
_ID_TAKEN: Final = "chats_pkey"
# Who may ask: a run costs money and reads everything the company holds.
_ASKERS: Final = (Role.OWNER, Role.OPS)
# The efforts a question may be asked at, whatever the assistant is configured with.
_EFFORTS: Final[tuple[Effort, ...]] = ("low", "medium", "high")


def get_assistant() -> AssistantOut:
    """What a question may be asked with: the model and effort the assistant is configured
    with, and the others settings offer (CHAT_MODELS, and the efforts every model takes)."""
    model, effort = llm.model_name(agents.ASSISTANT), llm.effort(agents.ASSISTANT)
    return AssistantOut(
        model=model,
        effort=effort,
        models=list(dict.fromkeys([model, *get_settings().chat_models])),
        efforts=list(dict.fromkeys([*_EFFORTS, effort])),
    )


async def list_chats(
    db: AsyncSession, actor: Actor, *, archived: bool, page: PageParams
) -> ChatPage:
    rows, total = await repository.list_chats(
        db, actor.company_id, actor.user_id, archived=archived, page=page
    )
    return ChatPage(
        items=[_describe(chat, unread=unread) for chat, unread in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def create_chat(
    db: AsyncSession, actor: Actor, *, chat_id: uuid.UUID | None, title: str
) -> ChatOut:
    """Opens a chat, under the id its client chose when it chose one. Asked twice with one id,
    the second is given the chat the first opened."""
    actor.require(*_ASKERS)
    if chat_id is not None:
        opened = await repository.get_chat(db, actor.company_id, chat_id)
        if opened is not None:
            return _describe(opened, unread=await repository.is_unread(db, opened, actor.user_id))
    chat = Chat(
        company_id=actor.company_id,
        origin=Origin.OWNER,
        title=" ".join(title.split()),
        opened_by=actor.user_id,
    )
    if chat_id is not None:
        chat.id = chat_id
    try:
        await repository.add(db, chat)
    except IntegrityError as exc:  # taken between the look and the insert, or another company's
        if violated_constraint(exc) != _ID_TAKEN:
            raise
        raise Conflict("This id is taken: choose another", field="id") from exc
    # Its opener has nothing in it left to read.
    await repository.mark_read(db, chat, actor.user_id, utcnow())
    await db.commit()
    return _describe(chat, unread=False)


async def get_chat(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> ChatOut:
    chat = await _chat(db, actor.company_id, chat_id)
    return _describe(chat, unread=await repository.is_unread(db, chat, actor.user_id))


async def update_chat(
    db: AsyncSession, actor: Actor, chat_id: uuid.UUID, *, title: str | None, archived: bool | None
) -> ChatOut:
    actor.require(*_ASKERS)
    chat = await _chat(db, actor.company_id, chat_id, for_update=True)
    if title is not None:
        name = " ".join(title.split())
        if not name:
            raise BadRequest("A chat's name has something in it", field="title")
        chat.title = name
    if archived is not None and archived != (chat.archived_at is not None):
        chat.archived_at = utcnow() if archived else None
    await db.commit()
    return _describe(chat, unread=await repository.is_unread(db, chat, actor.user_id))


async def delete_chat(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> None:
    actor.require(*_ASKERS)
    await repository.delete_chat(db, await _chat(db, actor.company_id, chat_id))
    await db.commit()


async def mark_read(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> None:
    """This user has read the chat up to now; nobody else's reading changes."""
    actor.require_person()
    chat = await _chat(db, actor.company_id, chat_id)
    await repository.mark_read(db, chat, actor.user_id, utcnow())
    await db.commit()


async def get_messages(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> ChatMessages:
    """What a reader is shown of a chat, as the chat protocol carries it: what was written,
    without the model's reasoning and without the notes code gave a turn Tamga took on its
    own, and each card where it was put, as an activity message of its kind. A chat that was
    compacted is shown whole, with a mark where its notes were written."""
    chat = await _chat(db, actor.company_id, chat_id)
    shown: list[CardMessage | CompactionMessage | dict[str, Any]] = []
    written: list[ModelMessage] = []

    def flush() -> None:
        shown.extend(
            message.model_dump(mode="json", by_alias=True, exclude_none=True)
            for message in AGUIAdapter.dump_messages(written)
            if message.role != _REASONING
        )
        written.clear()

    for row in await repository.rows(db, actor.company_id, chat.id):
        if row.kind is MessageKind.CARD:
            flush()
            shown.append(
                CardMessage(
                    id=str(row.id),
                    activity_type=CardKind(row.message["kind"]),
                    content=CardContent(id=row.message["id"]),
                )
            )
            continue
        if row.kind is MessageKind.NOTES:
            flush()
            shown.append(CompactionMessage(id=str(row.id)))
            continue
        message = context.loaded([row.message])[0]
        if row.began_by is Origin.TAMGA and isinstance(message, ModelRequest):
            said = [part for part in message.parts if not isinstance(part, UserPromptPart)]
            if not said:
                continue
            message = replace(message, parts=said)
        written.append(message)
    flush()
    return ChatMessages(messages=shown)


# A run


@dataclass
class _Reader:
    """Whoever reads a run's events as they come. A run does not need one: it finishes and is
    kept whether or not anybody is still there."""

    events: asyncio.Queue[BaseEvent | None] = field(default_factory=asyncio.Queue)


async def run_chat(
    db: AsyncSession, actor: Actor, chat_id: uuid.UUID, request: Request
) -> Response:
    """Runs the assistant in a chat on what the user just wrote, and streams what it does as the
    chat protocol's events. The run is given the chat's stored messages and, of what the client
    sent, only the new message; its tools act as this user.

    The question is kept before the model is asked, and the run is a task of its own: it
    finishes and its answer is kept even when its reader has gone. A chat has one run at a
    time. The most the run may ask is taken from the day's budget before the model is called.
    No transaction is open while the model works, and the request's own session is closed
    before the stream starts: it would hold a connection for as long as the answer takes."""
    actor.require(*_ASKERS)
    try:
        asked = AGUIAdapter.build_run_input(await request.body())
    except ValidationError as error:
        raise BadRequest("Not a run of the chat protocol") from error
    said = asked.messages[-1] if asked.messages else None
    if not isinstance(said, UserMessage) or not isinstance(said.content, str):
        raise BadRequest("A run answers a message in words: send one", field="messages")
    if not said.content.strip() or len(said.content) > MESSAGE_LENGTH:
        raise BadRequest("A message is 1 to 20,000 characters", field="messages")
    chosen = _chosen(asked.forwarded_props)
    question = ModelRequest(parts=[UserPromptPart(content=said.content)])
    run_id = uuid.uuid7()
    now = utcnow()
    # The row stays locked until the claim below commits, so two requests cannot both pass,
    # in this process or another.
    chat = await _chat(db, actor.company_id, chat_id, for_update=True)
    if is_running(chat, now):
        raise Conflict("Tamga is still answering in this chat")
    company = (await companies.get_companies(db, [actor.company_id])).get(actor.company_id)
    traces = company is not None and companies.traces_content(company.kind)
    admitted = False
    if context.due(chat):
        # The chat is nearly full: its earlier part is written into notes first. A compaction
        # that fails does not cost the owner their answer; the run is sent the chat as it is.
        try:
            await compact(db, chat, now, began_by=Origin.OWNER, trace_content=traces, whole=True)
        except TooManyRequests:
            raise
        except Exception as error:
            # The error's type only: a provider's error can quote what it was sent.
            logger.warning("compacting a chat before its run failed: %s", type(error).__name__)
        # The day's budget let the compaction begin, and the question it was made for is
        # answered with it: the budget's last request is not spent on preparing an answer
        # that is then refused.
        admitted = True
        # The compaction wrote through sessions of its own: the row is read again, and locked.
        now = utcnow()
        await db.refresh(chat, with_for_update=True)
        if is_running(chat, now):
            raise Conflict("Tamga is still answering in this chat")
    history = await context.sent(db, actor.company_id, chat.id)
    adapter = AGUIAdapter(
        agent=agents.assistant,
        # Of what the client sent, only the run's id: its messages, and any tool, state or
        # context it names, never reach the model. The question reaches it as the end of
        # the history, from the server's own copy.
        run_input=RunAgentInput(
            thread_id=str(chat_id),
            run_id=asked.run_id,
            messages=[],
            tools=[],
            context=[],
            forwarded_props=None,
        ),
        accept=request.headers.get("accept"),
    )
    seat = Seat(company_id=actor.company_id, thread_id=None, actor=actor, chat_id=chat_id)
    usage = RunUsage()
    reader = _Reader()

    async def keep(result: AgentRunResult[Any]) -> None:
        await _keep(actor, chat_id, seat, result.new_messages(), run_id, now)

    # Built before anything is written: a model that cannot be reached by its settings
    # fails here, with nothing reserved and no question left without its run.
    events = adapter.run_stream(
        message_history=[*history, question],
        conversation_id=str(chat_id),
        run_id=str(run_id),
        deps=seat,
        usage_limits=UsageLimits(request_limit=RUN_REQUESTS),
        usage=usage,
        on_complete=keep,
        **llm.stream_arguments(
            agents.ASSISTANT,
            # The assistant answers in the language it is written to.
            language=None,
            trace_content=traces,
            chosen_model=chosen.model,
            chosen_effort=chosen.effort,
        ),
    )
    await repository.lock_budget(db, actor.company_id)
    spent = await repository.requests_since(db, actor.company_id, Origin.OWNER, now - RUN_WINDOW)
    if not admitted and spent >= get_settings().chat_requests_per_day:
        raise TooManyRequests("The chat has no requests left today")
    await repository.add_run(
        db, actor.company_id, run_id, began_by=Origin.OWNER, requests=RUN_REQUESTS, at=now
    )
    await repository.add_messages(
        db,
        chat,
        context.dumped([question]),
        run_id=run_id,
        began_by=Origin.OWNER,
        user_id=actor.user_id,
    )
    naming = None
    # A digest the owner did not rename is named by its day, in the app.
    if not chat.title and chat.digest_on is None:
        chat.title = _title(said.content)
        # The name's one request is taken from the day's budget with the run's own, before
        # either model is called.
        named = uuid.uuid7()
        await repository.add_run(
            db, actor.company_id, named, began_by=Origin.OWNER, requests=1, at=now
        )
        naming = _name(
            actor.company_id, chat_id, named, said.content, chat.title, trace_content=traces
        )
    chat.last_message_at = now
    chat.running_since = now
    await repository.mark_read(db, chat, actor.user_id, now)
    await db.commit()
    await db.close()
    _runs[chat_id] = asyncio.create_task(
        _run(events, reader, chat_id, actor.company_id, run_id, now, usage, naming)
    )
    response = adapter.streaming_response(_read(reader))
    # A reverse proxy must pass each event on as it comes (ADR-0006).
    response.headers["X-Accel-Buffering"] = "no"
    return response


async def compact_chat(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> ChatOut:
    """The owner's own Compact: the chat's earlier part is written into notes now, whatever
    its size. The chat is not changed for its readers, who still see every message."""
    actor.require(*_ASKERS)
    now = utcnow()
    chat = await _chat(db, actor.company_id, chat_id, for_update=True)
    if is_running(chat, now):
        raise Conflict("Tamga is still answering in this chat")
    company = (await companies.get_companies(db, [actor.company_id])).get(actor.company_id)
    try:
        await compact(
            db,
            chat,
            now,
            began_by=Origin.OWNER,
            trace_content=company is not None and companies.traces_content(company.kind),
            # Asked for again with nothing new said, the last exchange still stays as it was.
            whole=False,
        )
    except llm.NoAnswer as error:
        raise Conflict("The notes could not be written: the chat is as it was") from error
    # The compaction wrote through sessions of its own: the row is read again.
    await db.refresh(chat)
    return _describe(chat, unread=await repository.is_unread(db, chat, actor.user_id))


async def compact(
    db: AsyncSession,
    chat: Chat,
    now: datetime,
    *,
    began_by: Origin,
    trace_content: bool,
    whole: bool,
) -> None:
    """Compacts a chat the caller holds locked and free: claims it as a run does, takes the
    most the compactor may ask from the day's budget of whoever it is for (the owner's
    questions, or the turns Tamga takes on its own), and commits the caller's transaction
    before the model is called. However it ends, the chat is free again and the budget is
    charged what was asked. ``whole``: see ``context.compact``."""
    company_id, chat_id, run_id = chat.company_id, chat.id, uuid.uuid7()
    settings = get_settings()
    budget = (
        settings.chat_requests_per_day
        if began_by is Origin.OWNER
        else settings.tamga_requests_per_day
    )
    await repository.lock_budget(db, company_id)
    if await repository.requests_since(db, company_id, began_by, now - RUN_WINDOW) >= budget:
        raise TooManyRequests("The chat has no requests left today")
    await repository.add_run(
        db, company_id, run_id, began_by=began_by, requests=context.COMPACT_REQUESTS, at=now
    )
    chat.running_since = now
    await db.commit()
    usage = RunUsage()
    try:
        await context.compact(
            company_id,
            chat_id,
            run_id=run_id,
            began=now,
            usage=usage,
            trace_content=trace_content,
            whole=whole,
        )
    finally:
        await end_run(company_id, chat_id, run_id, now, usage.requests)


async def stop_run(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> None:
    """Stops the run a chat has in flight, when it has one: the question stays, what the model
    had written of its answer does not, and the requests it made are counted."""
    actor.require(*_ASKERS)
    await _chat(db, actor.company_id, chat_id)
    run = _runs.get(chat_id)
    if run is not None:
        run.cancel()


async def _run(
    events: AsyncIterator[BaseEvent],
    reader: _Reader,
    chat_id: uuid.UUID,
    company_id: uuid.UUID,
    run_id: uuid.UUID,
    began: datetime,
    usage: RunUsage,
    naming: Coroutine[Any, Any, None] | None,
) -> None:
    """Drives a run to its end and hands its events, without the model's reasoning, to whoever
    reads. However it ends (its answer, an error, a stop), the chat is free for its next run
    and the day's budget is charged what the run asked, in place of the most it could have.
    ``naming`` names a chat that had no name, while its first question is answered."""
    named = None if naming is None else asyncio.create_task(naming)
    try:
        async for event in events:
            if isinstance(event, RunErrorEvent):
                # What failed stays in the server's log: a provider's error names models,
                # limits and ids. The reader is told only that the run failed.
                logger.warning("a chat run failed: %.300s", event.message)
                event = RunErrorEvent(
                    message=RUN_FAILED, code=RUN_FAILED, timestamp=event.timestamp
                )
            if not event.type.value.startswith(_REASONING_EVENTS):
                reader.events.put_nowait(event)
    finally:
        try:
            await end_run(company_id, chat_id, run_id, began, usage.requests)
            # The reader is let go once the chat has its name, so the list a client reads
            # when an answer ends shows it. The name is long written by then, as a rule.
            if named is not None:
                await named
        finally:
            # A run given up for dead may end after the chat took its next one, whose task
            # this is then not.
            if _runs.get(chat_id) is asyncio.current_task():
                del _runs[chat_id]
            reader.events.put_nowait(None)


def is_running(chat: Chat, now: datetime) -> bool:
    """Whether a run is being made in the chat: one began and has not ended, nor been given up."""
    return chat.running_since is not None and now - chat.running_since < RUN_STALE


async def end_run(
    company_id: uuid.UUID, chat_id: uuid.UUID, run_id: uuid.UUID, began: datetime, requests: int
) -> None:
    """A run is over, however it ended: its chat is free, and the day's budget is charged what
    it asked in place of the most it could have."""
    async with open_session() as db:
        await repository.settle_run(db, company_id, run_id, requests)
        await repository.end_run(db, company_id, chat_id, began)
        # The owner's other tabs and devices, and the members', see the chat move.
        await events.publish(db, company_id, events.EventKind.CHAT, chat_id)
        await db.commit()


async def _read(reader: _Reader) -> AsyncIterator[BaseEvent]:
    """A run's events for its reader, until the run ends or the reader goes."""
    while (event := await reader.events.get()) is not None:
        yield event


async def _keep(
    actor: Actor,
    chat_id: uuid.UUID,
    seat: Seat,
    written: list[ModelMessage],
    run_id: uuid.UUID,
    began: datetime,
) -> None:
    """Keeps what a finished run wrote: its messages after the chat's own, what the next run
    would be sent, and the records it opened. Nobody has read the answer until a reader says
    so (``mark_read``): a stream still open is a page left open, not a page looked at."""
    now = utcnow()
    async with open_session() as db:
        chat = await repository.get_chat(db, actor.company_id, chat_id, for_update=True)
        # Deleted while the model answered; or the run was given up for dead and the chat has
        # moved on: an answer to a history that is no longer the chat's is not put after it.
        if chat is None or chat.running_since != began:
            return
        await repository.add_messages(
            db,
            chat,
            context.dumped(written),
            run_id=run_id,
            began_by=Origin.OWNER,
            user_id=actor.user_id,
        )
        answers = [message for message in written if isinstance(message, ModelResponse)]
        if answers:
            last = answers[-1].usage
            chat.context_tokens = last.input_tokens + last.output_tokens
        chat.last_message_at = now
        for record_kind, record_id in dict.fromkeys(seat.opened):
            await repository.touch_record(db, chat, record_kind, record_id, now)
        await db.commit()


async def _merged(
    db: AsyncSession, company_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """A project merged into another is deleted. The chats that held it hold the one that
    stays, so what is found about that one is written where the owner already read of the
    job, and a card that showed the project that went shows the one that stays."""
    await repository.move_record(db, company_id, agents.PROJECT, source_id, target_id)
    for card in await repository.cards_showing(
        db, company_id, CardKind.PROJECT_PROPOSAL, source_id
    ):
        card.message = {**card.message, "id": str(target_id)}
    await db.flush()


projects.on_merge(_merged)


def _title(said: str) -> str:
    """The start of what the user first wrote, as the chat's name."""
    text = " ".join(said.split())
    return text if len(text) <= _TITLE_LENGTH else text[: _TITLE_LENGTH - 1] + "…"


async def _name(
    company_id: uuid.UUID,
    chat_id: uuid.UUID,
    run_id: uuid.UUID,
    said: str,
    held: str,
    *,
    trace_content: bool,
) -> None:
    """Has a model name a chat from the first message written in it: one request, of a small
    model, which the caller took from the day's budget as ``run_id`` and which is set here to
    what was asked. The chat takes the name only while it still holds ``held``, the start of
    that message, so a name the owner gave meanwhile stays. A name that could not be written
    is no error to anyone: the chat keeps the one it has."""
    usage = RunUsage()
    name = ""
    try:
        result = await llm.run(
            agents.titler,
            llm.fenced_text("The chat's first message", said[:_TITLE_READS]),
            deps=None,
            limits=UsageLimits(request_limit=1),
            # In the language the message is in.
            language=None,
            trace_content=trace_content,
            usage=usage,
        )
        name = _title(result.output.strip(_TITLE_WRAPPED))
        if not name:
            logger.warning("naming a chat gave no name")
    except Exception as error:
        # The error's type only: a provider's error can quote what it was sent.
        logger.warning("naming a chat failed: %s", type(error).__name__)
    async with open_session() as db:
        await repository.settle_run(db, company_id, run_id, usage.requests)
        if name:
            chat = await repository.get_chat(db, company_id, chat_id, for_update=True)
            if chat is not None and chat.title == held:
                chat.title = name
        await db.commit()


def _chosen(sent: Any) -> RunChoice:
    """The model and effort a run's sender chose for it. One that settings do not offer is
    refused, never replaced: nobody is answered by a model they did not choose, and a browser
    cannot name a model the company's settings leave out."""
    try:
        choice = RunChoice.model_validate(sent or {})
    except ValidationError as error:
        raise BadRequest("Not a choice of model and effort", field="forwardedProps") from error
    offered = get_assistant()
    if choice.model is not None and choice.model not in offered.models:
        raise BadRequest("This model is not offered here", field="forwardedProps")
    if choice.effort is not None and choice.effort not in offered.efforts:
        raise BadRequest("This effort is not offered here", field="forwardedProps")
    return choice


async def _chat(
    db: AsyncSession, company_id: uuid.UUID, chat_id: uuid.UUID, *, for_update: bool = False
) -> Chat:
    chat = await repository.get_chat(db, company_id, chat_id, for_update=for_update)
    if chat is None:
        raise NotFound("No such chat")
    return chat


def _describe(chat: Chat, *, unread: bool) -> ChatOut:
    return ChatOut(
        id=chat.id,
        title=chat.title,
        origin=chat.origin,
        unread=unread,
        archived=chat.archived_at is not None,
        last_message_at=chat.last_message_at,
        context_tokens=chat.context_tokens,
        context_window=get_settings().chat_context_tokens,
        running=is_running(chat, utcnow()),
        digest_on=chat.digest_on,
        created_at=chat.created_at,
    )
