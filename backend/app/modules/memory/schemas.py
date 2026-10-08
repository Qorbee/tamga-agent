import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.memory.models import MemoryKind, MemorySource, MemoryState

# The longest entry: one fact, not a document.
TEXT_LENGTH = 500


class MemoryEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: MemoryKind
    text: str
    party_id: uuid.UUID | None
    project_id: uuid.UUID | None
    source: MemorySource
    chat_id: uuid.UUID | None
    state: MemoryState
    written_by: uuid.UUID | None
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryPage(BaseModel):
    items: list[MemoryEntryOut]
    total: int


class MemoryIn(BaseModel):
    """An entry the owner writes in Settings: it holds as it is saved."""

    kind: MemoryKind
    text: str = Field(min_length=1, max_length=TEXT_LENGTH)
    party_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


class MemoryChange(BaseModel):
    """The owner's change to an entry: only the fields given change. ``held`` confirms a draft or
    takes a retired entry back; ``retired`` takes it out of every agent's reach. A firm or a
    project sent as null is cleared."""

    kind: MemoryKind | None = None
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_LENGTH)
    party_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    state: Literal[MemoryState.HELD, MemoryState.RETIRED] | None = None


MemorySort = Literal["-updated_at", "-last_used_at", "kind"]
