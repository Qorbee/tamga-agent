import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.books.schemas import InvoiceOut
from app.modules.conversations.models import Channel
from app.modules.conversations.schemas import MessageSummary
from app.modules.documents.schemas import DocumentOut
from app.modules.projects.models import (
    ChecklistMarkKind,
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
    "number",
    "-number",
    "name",
    "-name",
    "starts_on",
    "-starts_on",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
]


class ProjectJob(BaseModel):
    """The job as the project's conversations describe it together, worked out by code from what
    a model read in each: for each field what most of them say, and each firm in one role only.
    Firms go by their short names; the parties keep their full ones."""

    client: str | None = None
    subcontractors: list[str] = []
    consignee: str | None = None
    origin: str | None = None
    destination: str | None = None
    cargo: str | None = None
    trucks: int | None = None
    # Of the client and the subcontractors, those no card in the books is named as: named in
    # mail only, so a firm the books suggest in that role may be another.
    not_in_books: list[str] = []


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    # Earlier numbers the project had: mail quoting one still finds it.
    aliases: list[str]
    name: str
    # The job's span as far as it is known; null until someone or something says.
    starts_on: date | None
    ends_on: date | None
    status: ProjectStatus
    stage: ProjectStage
    field_sources: dict[str, FieldSource]
    # Links to it the owner confirmed, and those that still wait for the owner.
    confirmed_links: int
    suggested_links: int
    # Null while nothing was read of its job.
    job: ProjectJob | None
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
    # Sent as null, a date is cleared; left out, it stays.
    starts_on: date | None = None
    ends_on: date | None = None
    # The owner's moves: confirm a draft or a proposal, reject a proposal, close a project.
    status: Literal["active", "rejected", "closed"] | None = None


class ProjectMerge(BaseModel):
    """The owner saying this project and ``into`` are one job: ``into`` stays."""

    into: uuid.UUID


class NumberingOut(BaseModel):
    format: str
    # What the next project would be numbered, if nothing else takes it first.
    next_number: str


class NumberingUpdate(BaseModel):
    format: str = Field(min_length=1, max_length=60)
    # Where the counter continues, so generated numbers follow the owner's own last one.
    next_seq: int | None = Field(default=None, ge=1, le=10**9)


class ItemRef(BaseModel):
    item_kind: ItemKind
    item_id: uuid.UUID


class ProjectPartyOut(BaseModel):
    """A firm's role on a project: the owner's, or code's suggestion with what names it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    party_id: uuid.UUID
    party_name: str
    role: ProjectRole
    layer: int
    amount: Decimal | None
    currency: str | None
    # Never "rejected" in a list: only the answer that refused it says so.
    state: Literal["confirmed", "suggested", "rejected"]
    # A suggestion's kinds of signal: "reading" (a conversation's reading names the firm in the
    # role), "sender" (its mail came from the firm's address), "invoice" (the firm's invoice is
    # on the project by its number). Two agree: the project's firm until the owner says no.
    signals: list[str]
    # The items those signals were read from.
    evidence: list[ItemRef]


class ProjectPartyAnswer(BaseModel):
    """The owner's answer to a suggested firm."""

    state: Literal["confirmed", "rejected"]


class ProjectPartyCreate(BaseModel):
    project_id: uuid.UUID
    party_id: uuid.UUID
    role: ProjectRole
    layer: int = Field(default=0, ge=0, le=10)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class MessageBrief(BaseModel):
    """A linked mail, for the project's timeline."""

    item_kind: Literal["message"]
    at: datetime
    channel: Channel
    message: MessageSummary


class DocumentBrief(BaseModel):
    """A linked file, for the project's timeline: when and how it came."""

    item_kind: Literal["document"]
    # When the first mail that brought it was sent, else when it was uploaded.
    at: datetime
    channel: Channel
    document: DocumentOut
    # The first mail that brought it, itself or inside an archive; null for an upload.
    message_id: uuid.UUID | None


class InvoiceBrief(BaseModel):
    """A linked invoice, for the project's timeline."""

    item_kind: Literal["invoice"]
    # An invoice has a day, not an hour: its issue day at noon UTC, which falls on that day for a
    # reader anywhere from UTC-11 to UTC+11; when it was read from the books if it has no day.
    at: datetime
    channel: Literal["books"]
    invoice: InvoiceOut


ItemBrief = Annotated[MessageBrief | DocumentBrief | InvoiceBrief, Field(discriminator="item_kind")]


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
    # What the item is, with `expand=item`; null otherwise, or when the item is gone.
    item: ItemBrief | None = None


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


ChecklistState = Literal["done", "waiting", "missing", "not_needed"]


class ChecklistMarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    shipment_id: uuid.UUID | None
    key: str
    mark: ChecklistMarkKind
    updated_at: datetime


class ChecklistItemOut(BaseModel):
    """One item of a project's or a truck's checklist, as it stands: ``done`` (a confirmed paper,
    or the owner's mark), ``waiting`` (a paper suggested, a proof not yet marked, a bill not yet
    paid), ``missing``, or ``not_needed``. ``link_ids`` are the project links that fill it."""

    project_id: uuid.UUID
    shipment_id: uuid.UUID | None
    key: str
    state: ChecklistState
    # Whether the job asks for it: the template's default, or the owner's mark.
    required: bool
    link_ids: list[uuid.UUID]
    mark: ChecklistMarkOut | None


class ChecklistMarkSet(BaseModel):
    project_id: uuid.UUID
    # A truck's item; null for the project's own.
    shipment_id: uuid.UUID | None = None
    key: str = Field(min_length=1, max_length=40)
    mark: ChecklistMarkKind
