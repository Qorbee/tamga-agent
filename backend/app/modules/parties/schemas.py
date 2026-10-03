import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.parties.models import PartyKind

# "source": the parties synced from the books first, then by name.
PartySort = Literal["source", "name", "-name", "created_at", "-created_at"]


class PartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: PartyKind
    # The accounting system it is synced from, or null for one read from mail or added by hand.
    source: str | None
    created_at: datetime


class PartyPage(BaseModel):
    items: list[PartyOut]
    total: int
    limit: int
    offset: int
