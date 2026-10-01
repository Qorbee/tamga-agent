import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.assistant.models import Origin


class ChatOut(BaseModel):
    id: uuid.UUID
    title: str
    # Who opened it. Either way it is an ordinary chat: anything may be asked in it.
    origin: Origin
    # Whether it holds a message this user has not read.
    unread: bool
    archived: bool
    # Null while the chat holds no message.
    last_message_at: datetime | None
    # How full its context is: what its next run would be sent, of what a chat may hold.
    context_tokens: int
    context_window: int
    # Whether Tamga is answering in it now.
    running: bool
    created_at: datetime


class ChatPage(BaseModel):
    items: list[ChatOut]
    total: int
    limit: int
    offset: int


class ChatCreate(BaseModel):
    # Chosen by the client, so a chat has its id before its first message is sent, and asking
    # twice opens one chat. Left out, the server draws one.
    id: uuid.UUID | None = None
    title: str = Field(default="", max_length=200)


class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class ChatMessages(BaseModel):
    """A chat's messages as the chat protocol (AG-UI) carries them, oldest first: what a client
    hands its runtime to show the chat. The server keeps its own copy and never reads a client's
    back (ADR-0006)."""

    messages: list[dict[str, Any]]
