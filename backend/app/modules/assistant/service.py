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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final

from ag_ui.core import BaseEvent, RunAgentInput, RunErrorEvent, UserMessage
from fastapi import Request
from fastapi.responses import Response
from pydantic import ValidationError
from pydantic_ai import UsageLimits
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.usage import RunUsage
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.assistant import agents, repository
from app.modules.assistant.models import Chat, Origin
from app.modules.assistant.schemas import ChatMessages, ChatOut, ChatPage
from app.modules.companies import service as companies
from app.platform import llm
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
# A chat with no title takes the start of its first message.
_TITLE_LENGTH: Final = 60
# The model's reasoning is kept with its messages, so its next run has it, and is not sent to
# a client: the protocol carries it as an opaque value for a client to hand back, and no client
# hands this server anything back.
_REASONING: Final = "reasoning"
_REASONING_EVENTS: Final = "REASONING_"
# The run each chat has in flight, in this process: None from the moment a run is admitted
# until its task starts. The API is one process; a second one would need this in Postgres.
_runs: Final[dict[uuid.UUID, asyncio.Task[None] | None]] = {}
# The constraint a chat's id breaks when a chat already has it.
_ID_TAKEN: Final = "chats_pkey"
# Who may ask: a run costs money and reads everything the company holds.
_ASKERS: Final = (Role.OWNER, Role.OPS)


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
    chat = await _chat(db, actor.company_id, chat_id)
    await repository.mark_read(db, chat, actor.user_id, utcnow())
    await db.commit()


async def get_messages(db: AsyncSession, actor: Actor, chat_id: uuid.UUID) -> ChatMessages:
    chat = await _chat(db, actor.company_id, chat_id)
    history = _loaded(await repository.messages(db, actor.company_id, chat.id))
    return ChatMessages(
        messages=[
            message.model_dump(mode="json", by_alias=True, exclude_none=True)
            for message in AGUIAdapter.dump_messages(history)
            if message.role != _REASONING
        ]
    )


# A run


@dataclass
class _Reader:
    """Whoever reads a run's events as they come. A run does not need one: it finishes and is
    kept whether or not anybody is still there."""

    events: asyncio.Queue[BaseEvent | None] = field(default_factory=asyncio.Queue)
    # False once the stream was closed before the run ended: a phone that slept, a closed tab.
    present: bool = True


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
    question = ModelRequest(parts=[UserPromptPart(content=said.content)])
    run_id = uuid.uuid7()
    now = utcnow()
    chat = await _chat(db, actor.company_id, chat_id, for_update=True)
    # No await between the look and the claim, so two requests cannot both pass.
    if chat_id in _runs:
        raise Conflict("Tamga is still answering in this chat")
    _runs[chat_id] = None
    try:
        history = _loaded(await repository.messages(db, actor.company_id, chat.id))
        company = (await companies.get_companies(db, [actor.company_id])).get(actor.company_id)
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
        seat = agents.Seat(
            company_id=actor.company_id, thread_id=None, actor=actor, chat_id=chat_id
        )
        usage = RunUsage()
        reader = _Reader()

        async def keep(result: AgentRunResult[Any]) -> None:
            await _keep(actor, chat_id, seat, result.new_messages(), run_id, read=reader.present)

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
                trace_content=company is not None and companies.traces_content(company.kind),
            ),
        )
        await repository.lock_budget(db, actor.company_id)
        spent = await repository.requests_since(db, actor.company_id, now - RUN_WINDOW)
        if spent >= get_settings().chat_requests_per_day:
            raise TooManyRequests("The chat has no requests left today")
        await repository.add_run(
            db, actor.company_id, run_id, began_by=Origin.OWNER, requests=RUN_REQUESTS, at=now
        )
        await repository.add_messages(
            db,
            chat,
            _dumped([question]),
            run_id=run_id,
            began_by=Origin.OWNER,
            user_id=actor.user_id,
        )
        if not chat.title:
            chat.title = _title(said.content)
        chat.last_message_at = now
        await repository.mark_read(db, chat, actor.user_id, now)
        await db.commit()
        await db.close()
        _runs[chat_id] = asyncio.create_task(
            _run(events, reader, chat_id, actor.company_id, run_id, usage)
        )
    except BaseException:
        del _runs[chat_id]
        raise
    response = adapter.streaming_response(_read(reader))
    # A reverse proxy must pass each event on as it comes (ADR-0006).
    response.headers["X-Accel-Buffering"] = "no"
    return response


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
    usage: RunUsage,
) -> None:
    """Drives a run to its end and hands its events, without the model's reasoning, to whoever
    reads. However it ends (its answer, an error, a stop), the chat is free for its next run
    and the day's budget is charged what the run asked, in place of the most it could have."""
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
            async with open_session() as db:
                await repository.settle_run(db, company_id, run_id, usage.requests)
                await db.commit()
        finally:
            _runs.pop(chat_id, None)
            reader.events.put_nowait(None)


async def _read(reader: _Reader) -> AsyncIterator[BaseEvent]:
    """A run's events for its reader, until the run ends or the reader goes."""
    ended = False
    try:
        while (event := await reader.events.get()) is not None:
            yield event
        ended = True
    finally:
        reader.present = ended


async def _keep(
    actor: Actor,
    chat_id: uuid.UUID,
    seat: agents.Seat,
    written: list[ModelMessage],
    run_id: uuid.UUID,
    *,
    read: bool,
) -> None:
    """Keeps what a finished run wrote: its messages after the chat's own, what the next run
    would be sent, and the records it opened. The user who asked has read it when they were
    still there to read it."""
    now = utcnow()
    async with open_session() as db:
        chat = await repository.get_chat(db, actor.company_id, chat_id, for_update=True)
        if chat is None:  # deleted while the model answered
            return
        await repository.add_messages(
            db, chat, _dumped(written), run_id=run_id, began_by=Origin.OWNER, user_id=actor.user_id
        )
        answers = [message for message in written if isinstance(message, ModelResponse)]
        if answers:
            last = answers[-1].usage
            chat.context_tokens = last.input_tokens + last.output_tokens
        chat.last_message_at = now
        for record_kind, record_id in dict.fromkeys(seat.opened):
            await repository.touch_record(db, chat, record_kind, record_id, now)
        if read:
            await repository.mark_read(db, chat, actor.user_id, now)
        await db.commit()


def _title(said: str) -> str:
    """The start of what the user first wrote, as the chat's name."""
    text = " ".join(said.split())
    return text if len(text) <= _TITLE_LENGTH else text[: _TITLE_LENGTH - 1] + "…"


def _dumped(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    return stored


def _loaded(stored: list[dict[str, Any]]) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(stored)


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
        running=chat.id in _runs,
        created_at=chat.created_at,
    )
