import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import Response

from app.modules.assistant import service
from app.modules.assistant.schemas import ChatCreate, ChatMessages, ChatOut, ChatPage, ChatUpdate
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

chats = APIRouter(tags=["chats"])


@chats.get("/chats")
async def list_chats(
    actor: ActorDep, db: SessionDep, page: PageDep, archived: bool = False
) -> ChatPage:
    """The company's chats: those this user has not read first, then the most recent."""
    return await service.list_chats(db, actor, archived=archived, page=page)


@chats.post("/chats", status_code=status.HTTP_201_CREATED)
async def create_chat(body: ChatCreate, actor: ActorDep, db: SessionDep) -> ChatOut:
    """Opens a chat. Sent again with the same id, it gives the chat that id already opened."""
    return await service.create_chat(db, actor, chat_id=body.id, title=body.title)


@chats.get("/chats/{chat_id}")
async def get_chat(chat_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ChatOut:
    return await service.get_chat(db, actor, chat_id)


@chats.patch("/chats/{chat_id}")
async def update_chat(
    chat_id: uuid.UUID, body: ChatUpdate, actor: ActorDep, db: SessionDep
) -> ChatOut:
    return await service.update_chat(db, actor, chat_id, title=body.title, archived=body.archived)


@chats.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> None:
    await service.delete_chat(db, actor, chat_id)


@chats.put("/chats/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(chat_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> None:
    await service.mark_read(db, actor, chat_id)


@chats.get("/chats/{chat_id}/messages")
async def get_messages(chat_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ChatMessages:
    return await service.get_messages(db, actor, chat_id)


# The body and the answer are the chat protocol's (AG-UI), whose models both sides take from
# the protocol's own packages: named here, not copied into this contract.
_RUN: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "AG-UI RunAgentInput",
                }
            }
        },
    },
    "responses": {
        "200": {
            "description": "The run's AG-UI events, as they come",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
}


@chats.post("/chats/{chat_id}/runs", response_class=Response, openapi_extra=_RUN)
async def run_chat(
    chat_id: uuid.UUID, request: Request, actor: ActorDep, db: SessionDep
) -> Response:
    """Answers the message the body carries, as a stream of the chat protocol's events (AG-UI).
    The body is the protocol's run input; of its messages only the last, the new one, is read."""
    return await service.run_chat(db, actor, chat_id, request)


@chats.post("/chats/{chat_id}/compact")
async def compact_chat(chat_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> ChatOut:
    """Writes the chat's earlier part into notes, which its runs are then sent in place of
    those messages. It happens by itself before a run once the chat nears its window."""
    return await service.compact_chat(db, actor, chat_id)


@chats.delete("/chats/{chat_id}/runs/current", status_code=status.HTTP_204_NO_CONTENT)
async def stop_run(chat_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> None:
    """Stops the answer being written in the chat, when one is."""
    await service.stop_run(db, actor, chat_id)
