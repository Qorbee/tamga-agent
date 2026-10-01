import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.assistant.models import CardKind, Origin


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


class CardContent(BaseModel):
    # The record the card shows; its kind says which (``CardKind``).
    id: uuid.UUID


class CardMessage(BaseModel):
    """A card code put in a chat, carried as the chat protocol's activity message. A client
    draws it from the record it names, which it reads like any other."""

    id: str
    role: Literal["activity"] = "activity"
    activity_type: CardKind = Field(serialization_alias="activityType")
    content: CardContent


class ChatMessages(BaseModel):
    """A chat's messages as the chat protocol (AG-UI) carries them, oldest first: what a client
    hands its runtime to show the chat. The server keeps its own copy and never reads a client's
    back (ADR-0006)."""

    # The protocol's own messages, as its client library types them, and Tamga's cards.
    messages: list[CardMessage | dict[str, Any]]
