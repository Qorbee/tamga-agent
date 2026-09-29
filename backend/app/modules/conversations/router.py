import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.modules.conversations import service
from app.modules.conversations.models import Channel, Direction
from app.modules.conversations.schemas import MessageOut, MessagePage, ThreadPage
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

messages = APIRouter(tags=["messages"])
threads = APIRouter(tags=["threads"])


@messages.get("/messages")
async def list_messages(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    direction: Direction | None = None,
    channel: Channel | None = None,
    # One conversation's mails, from a ThreadSummary or a MessageSummary.
    thread_key: Annotated[str | None, Query(max_length=1000)] = None,
) -> MessagePage:
    return await service.list_messages(
        db, actor, q=q, direction=direction, channel=channel, thread_key=thread_key, page=page
    )


@messages.get("/messages/{message_id}")
async def get_message(message_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> MessageOut:
    return await service.get_message(db, actor, message_id)


@messages.get(
    "/messages/{message_id}/raw",
    response_class=Response,
    responses={200: {"content": {"message/rfc822": {}}, "description": "The original mail"}},
)
async def get_message_raw(message_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> Response:
    """The mail as its server gave it (.eml), headers and every part, for a mail client."""
    raw = await service.get_raw(db, actor, message_id)
    return Response(
        content=raw,
        media_type="message/rfc822",
        headers={
            "Content-Disposition": f'attachment; filename="{message_id}.eml"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
        },
    )


@threads.get("/threads")
async def list_threads(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> ThreadPage:
    """Conversations, the most recently active first; ``q`` matches any of their mails."""
    return await service.list_threads(db, actor, q=q, page=page)
