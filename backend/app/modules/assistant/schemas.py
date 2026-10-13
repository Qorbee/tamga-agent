import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import Effort
from app.modules.assistant.models import CardKind, Origin
from app.modules.memory.schemas import MemoryChangeDone


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
    # The day whose digest it is: what Tamga found past that day's new chats. The app names a
    # digest the owner has not renamed by its day.
    digest_on: date | None
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
    # A card of several records (``decision_batch``): each one's id, and the evaluation Tamga
    # wrote about, so a client marks one evaluated again since.
    ids: list[uuid.UUID] = []
    evaluations: list[int] = []


class CardMessage(BaseModel):
    """A card code put in a chat, carried as the chat protocol's activity message. A client
    draws it from the record it names, which it reads like any other."""

    id: str
    role: Literal["activity"] = "activity"
    activity_type: CardKind = Field(serialization_alias="activityType")
    content: CardContent


class CompactionMessage(BaseModel):
    """Where a chat was compacted, carried as an activity message: runs are sent notes in place
    of the messages above it, which a reader still sees."""

    id: str
    role: Literal["activity"] = "activity"
    activity_type: Literal["compaction"] = Field(
        default="compaction", serialization_alias="activityType"
    )
    content: dict[str, Any] = {}


class ChatMessages(BaseModel):
    """A chat's messages as the chat protocol (AG-UI) carries them, oldest first: what a client
    hands its runtime to show the chat. The server keeps its own copy and never reads a client's
    back (ADR-0006)."""

    # The protocol's own messages, as its client library types them, and Tamga's cards.
    messages: list[CardMessage | CompactionMessage | dict[str, Any]]


class AssistantOut(BaseModel):
    """What whoever asks in a chat may choose for an answer, and what an answer is given when
    nothing is chosen."""

    model: str
    effort: Effort
    # Each as settings name it, ``provider:name``; the configured one first.
    models: list[str]
    efforts: list[Effort]


class RunChoice(BaseModel):
    """What a run's sender chose for it, of what ``AssistantOut`` offers: carried in the chat
    protocol's ``forwardedProps``. What is left out is as configured."""

    model: str | None = None
    effort: Effort | None = None


class MemoryText(BaseModel):
    """What someone wrote in Settings for Tamga to remember, change or forget, in their own words
    (ADR-0030). ``entry_id``: the entry an edit is about."""

    text: str = Field(min_length=1, max_length=2000)
    entry_id: uuid.UUID | None = None


class MemoryChanges(BaseModel):
    # In the order made; none when what was written changed nothing.
    changes: list[MemoryChangeDone]
    # The memorist's one sentence: what it did, or why it changed nothing.
    reason: str
