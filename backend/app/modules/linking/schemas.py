import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from app.modules.conversations.schemas import ThreadSummary
from app.modules.linking.models import Outcome


class JobReading(BaseModel):
    """What a model read in a conversation: a proposal for the owner, never a fact."""

    # Who the company deals with in the conversation, and what that firm is to it in this job.
    party: str | None = None
    party_role: Literal["client", "subcontractor", "other"] | None = None
    # Of the job: who gave it, who it was given on to, who receives the goods, the route, the
    # load, how many trucks, the first day of loading and the last day of delivery.
    client: str | None = None
    subcontractor: str | None = None
    consignee: str | None = None
    origin: str | None = None
    destination: str | None = None
    cargo: str | None = None
    trucks: int | None = None
    starts_on: date | None = None
    ends_on: date | None = None


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
    this project, whatever was suggested; with ``from_project_id`` it is moved there."""

    links: Literal["confirmed", "rejected"] | None = None
    project_id: uuid.UUID | None = None
    # With project_id, to move a conversation out of the project it is in: that project, as the
    # owner saw it. What was confirmed there moves too; placed elsewhere since, it is refused.
    from_project_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if (self.links is None) == (self.project_id is None):
            raise ValueError("give either links or project_id")
        if self.from_project_id is not None and self.project_id is None:
            raise ValueError("from_project_id goes with project_id")
        return self
