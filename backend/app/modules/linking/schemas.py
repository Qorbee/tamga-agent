import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from app.modules.conversations.schemas import ThreadSummary
from app.modules.linking.models import Outcome


class JobReading(BaseModel):
    """What a model read in a conversation: a proposal for the owner, never a fact."""

    # Who the company deals with in the conversation, and what that firm is to it in this job.
    party: str | None = None
    party_role: Literal["client", "subcontractor", "other"] | None = None
    # Of a job a project was proposed for: who gave it, who it was given on to, the route, the load.
    client: str | None = None
    subcontractor: str | None = None
    origin: str | None = None
    destination: str | None = None
    cargo: str | None = None


class PlacementOut(BaseModel):
    """Where the linker placed a conversation, and why."""

    id: uuid.UUID
    thread_id: uuid.UUID
    # New mail, or a new sorting, since it was last read.
    waiting: bool
    # Null until it was read once.
    outcome: Outcome | None
    # "keys", "thread", "model:linker", "unanswered", or "owner".
    placed_by: str | None
    project_id: uuid.UUID | None
    # asked: the projects the owner chooses between.
    alternatives: list[uuid.UUID]
    reason: str
    confidence: float | None
    # The mails the reason rests on.
    cited: list[uuid.UUID]
    reading: JobReading | None
    placed_at: datetime | None
    # Links between its mails and its project that still wait for the owner.
    suggested_links: int
    # Null when its mail is gone.
    conversation: ThreadSummary | None


class PlacementPage(BaseModel):
    items: list[PlacementOut]
    total: int
    limit: int
    offset: int


class PlacementDecision(BaseModel):
    """The owner's answer, one of two. ``links``: every link that waits between the conversation's
    mails and its project is confirmed or rejected. ``project_id``: the conversation belongs to
    this project, whatever was suggested."""

    links: Literal["confirmed", "rejected"] | None = None
    project_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if (self.links is None) == (self.project_id is None):
            raise ValueError("give either links or project_id")
        return self
