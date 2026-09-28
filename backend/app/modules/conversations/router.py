import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.conversations import service
from app.modules.conversations.models import Channel, Direction
from app.modules.conversations.schemas import MessageOut, MessagePage
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

messages = APIRouter(tags=["messages"])


@messages.get("/messages")
async def list_messages(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    direction: Direction | None = None,
    channel: Channel | None = None,
) -> MessagePage:
    return await service.list_messages(
        db, actor, q=q, direction=direction, channel=channel, page=page
    )


@messages.get("/messages/{message_id}")
async def get_message(message_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> MessageOut:
    return await service.get_message(db, actor, message_id)
