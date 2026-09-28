import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.projects.models import (
    FieldSource,
    LinkMethod,
    LinkStatus,
    ProjectRole,
    ProjectStage,
    ProjectStatus,
    ProposalStatus,
    ProposalTarget,
)
from app.platform.items import ItemKind

ProjectSort = Literal[
    "number", "-number", "name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"
]


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    # Earlier numbers the project had: mail quoting one still finds it.
    aliases: list[str]
    name: str
    status: ProjectStatus
    stage: ProjectStage
    field_sources: dict[str, FieldSource]
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    total: int
    limit: int
    offset: int


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    # Left out, the next number in the company's format is drawn.
    number: str | None = Field(default=None, min_length=1, max_length=60)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    number: str | None = Field(default=None, min_length=1, max_length=60)
    # The owner's moves: confirm a draft or a proposal, reject a proposal, close a project.
    status: Literal["active", "rejected", "closed"] | None = None


class NumberingOut(BaseModel):
    format: str
    # What the next project would be numbered, if nothing else takes it first.
    next_number: str


class NumberingUpdate(BaseModel):
    format: str = Field(min_length=1, max_length=60)
    # Where the counter continues, so generated numbers follow the owner's own last one.
    next_seq: int | None = Field(default=None, ge=1, le=10**9)


class ProjectPartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    party_id: uuid.UUID
    party_name: str
    role: ProjectRole
    layer: int
    amount: Decimal | None
    currency: str | None


class ProjectPartyCreate(BaseModel):
    project_id: uuid.UUID
    party_id: uuid.UUID
    role: ProjectRole
    layer: int = Field(default=0, ge=0, le=10)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_kind: ItemKind
    item_id: uuid.UUID
    project_id: uuid.UUID
    shipment_id: uuid.UUID | None
    method: LinkMethod
    confidence: float
    evidence: list[uuid.UUID]
    status: LinkStatus
    proof_of_delivery: bool
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime


class LinkPage(BaseModel):
    items: list[LinkOut]
    total: int
    limit: int
    offset: int


class LinkCreate(BaseModel):
    """The owner attaching an item to a project by hand: confirmed at once."""

    item_kind: Literal["message", "document", "invoice"]
    item_id: uuid.UUID
    project_id: uuid.UUID
    shipment_id: uuid.UUID | None = None
    proof_of_delivery: bool = False


class LinkUpdate(BaseModel):
    status: Literal["confirmed", "rejected"] | None = None
    # Which truck the item belongs to; null to detach it from one.
    shipment_id: uuid.UUID | None = None
    proof_of_delivery: bool | None = None


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target: ProposalTarget
    target_id: uuid.UUID
    field: str
    value: Any
    source: FieldSource
    evidence: list[uuid.UUID]
    status: ProposalStatus
    decided_at: datetime | None
    created_at: datetime


class ProposalPage(BaseModel):
    items: list[ProposalOut]
    total: int
    limit: int
    offset: int


class ProposalDecision(BaseModel):
    status: Literal["accepted", "dismissed"]
