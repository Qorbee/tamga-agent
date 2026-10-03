import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.items import ItemKind
from app.platform.tenancy import CompanyScoped


class ProjectStatus(StrEnum):
    # Created by the owner (in chat or on the board); empty shipments do not make it active.
    DRAFT = "draft"
    # Created by the linker from evidence; the owner confirms or rejects it.
    PROPOSED = "proposed"
    ACTIVE = "active"
    CLOSED = "closed"
    REJECTED = "rejected"


class ProjectStage(StrEnum):
    """Where the job stands, in order. Computed from its shipments and invoices, never typed."""

    ORDERED = "ordered"
    LOADED = "loaded"
    DELIVERED = "delivered"
    DOCUMENTS = "documents"
    INVOICED = "invoiced"
    CLIENT_PAID = "client_paid"
    SUBCONTRACTOR_PAID = "subcontractor_paid"


class FieldSource(StrEnum):
    """Where an editable field's value came from; the owner's always wins (ADR-0010)."""

    OWNER = "owner"
    EXTRACTION = "extraction"
    SUGGESTION = "suggestion"


class ProjectRole(StrEnum):
    CLIENT = "client"
    SUBCONTRACTOR = "subcontractor"
    DRIVER = "driver"
    DISPATCHER = "dispatcher"
    CONSIGNEE = "consignee"


class LinkMethod(StrEnum):
    # A shared strong key: a project, order, invoice, ETTN or CMR number, a plate within the dates.
    KEY = "key"
    # The same party within a date window, scored by route and amount.
    SCORED = "scored"
    AGENT = "agent"
    # The owner's answer, or the owner's own link: code never changes it again (ADR-0021).
    OWNER = "owner"
    # A file that came in a mail of the project: its status follows that mail's links
    # (`projects.carry_files`, ADR-0021).
    MAIL = "mail"


class LinkStatus(StrEnum):
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ProposalTarget(StrEnum):
    PROJECT = "project"
    # Logistics pack: the shipment's fields are written through the target it registers.
    SHIPMENT = "shipment"


class ProposalStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class Project(CompanyScoped, Base):
    # Server defaults (created_at, updated_at) come back with the write, not by a lazy load.
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("company_id", "number"),
        # The target of every (company_id, project_id) key, which ties a child to one company.
        UniqueConstraint("company_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # In the company's own format (project_numbering), editable; every number it ever had stays in
    # project_numbers, so late mail quoting an old one still finds it.
    number: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[ProjectStatus] = mapped_column(text_enum(ProjectStatus, "projects_status_check"))
    stage: Mapped[ProjectStage] = mapped_column(
        text_enum(ProjectStage, "projects_stage_check", length=24), default=ProjectStage.ORDERED
    )
    # The job's span as far as it is known: for a freight job, the first day of loading and the
    # last day of delivery. Editable like the name, each with its source below.
    starts_on: Mapped[date | None]
    ends_on: Mapped[date | None]
    # Field name → FieldSource: who set each editable field, so an extraction never overwrites the
    # owner's value (it becomes a field_proposals row instead).
    field_sources: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ProjectNumber(CompanyScoped, Base):
    """Every number a project has had, the current one included. Unique per company on the
    normalised form, so a number once used is never given to another project."""

    __tablename__ = "project_numbers"
    __table_args__ = (
        UniqueConstraint("company_id", "normalised"),
        ForeignKeyConstraint(
            ["company_id", "project_id"], ["projects.company_id", "projects.id"], ondelete="CASCADE"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[str] = mapped_column(String(60))
    normalised: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProjectNumbering(CompanyScoped, Base):
    """The company's number format and its counter; one row per company, locked while a number is
    drawn."""

    __tablename__ = "project_numbering"
    __table_args__ = (UniqueConstraint("company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # Tokens: {yyyy}, {yy}, and {seq} or {seq:N} (zero-padded to N digits). See numbering.py.
    format: Mapped[str] = mapped_column(String(60))
    next_seq: Mapped[int] = mapped_column(default=1)


class ProjectParty(CompanyScoped, Base):
    """What a party is to one project: its client, a subcontractor, a driver. One project can have
    layers (a client above, subcontractors below), each with its agreed amount."""

    # Server defaults (created_at, updated_at) come back with the write, not by a lazy load.
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "project_parties"
    __table_args__ = (
        UniqueConstraint("project_id", "party_id", "role"),
        ForeignKeyConstraint(
            ["company_id", "project_id"], ["projects.company_id", "projects.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["company_id", "party_id"], ["parties.company_id", "parties.id"], ondelete="CASCADE"
        ),
        CheckConstraint(
            "(amount IS NULL) = (currency IS NULL)", name="project_parties_amount_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    party_id: Mapped[uuid.UUID]
    role: Mapped[ProjectRole] = mapped_column(text_enum(ProjectRole, "project_parties_role_check"))
    # 0 is the client's layer; each subcontracting step below it adds one.
    layer: Mapped[int] = mapped_column(default=0)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProjectLink(CompanyScoped, Base):
    """Evidence tying an item (a message, document, invoice or position) to a project and, when
    known, to one of its shipments. Only a confirmed link is proof for money (ADR-0010)."""

    # Server defaults (created_at, updated_at) come back with the write, not by a lazy load.
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "project_links"
    __table_args__ = (
        UniqueConstraint("project_id", "item_kind", "item_id"),
        ForeignKeyConstraint(
            ["company_id", "project_id"], ["projects.company_id", "projects.id"], ondelete="CASCADE"
        ),
        # The logistics pack's table, named in the database only: the shipment must belong to the
        # same project and company. A null shipment_id leaves the key unchecked (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["company_id", "project_id", "shipment_id"],
            ["shipments.company_id", "shipments.project_id", "shipments.id"],
        ),
        # One truck's proof can never clear two bills.
        Index(
            "project_links_one_confirmed_proof",
            "company_id",
            "item_kind",
            "item_id",
            unique=True,
            postgresql_where=text(
                "status = 'confirmed' AND proof_of_delivery AND shipment_id IS NOT NULL"
            ),
        ),
        Index(None, "company_id", "item_kind", "item_id"),
        Index(None, "company_id", "status"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="project_links_confidence_check"
        ),
        CheckConstraint(
            "(status = 'suggested') = (decided_at IS NULL)", name="project_links_decided_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    item_kind: Mapped[ItemKind] = mapped_column(
        text_enum(ItemKind, "project_links_item_kind_check")
    )
    item_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    shipment_id: Mapped[uuid.UUID | None]
    method: Mapped[LinkMethod] = mapped_column(text_enum(LinkMethod, "project_links_method_check"))
    confidence: Mapped[float]
    # The ids of the facts the link relied on.
    evidence: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=text("'{}'")
    )
    status: Mapped[LinkStatus] = mapped_column(text_enum(LinkStatus, "project_links_status_check"))
    # A CMR or signed delivery note: at most one confirmed shipment link.
    proof_of_delivery: Mapped[bool] = mapped_column(default=False)
    # Null with a decided_at: decided by code (a key link above the threshold, a replay).
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class FieldProposal(CompanyScoped, Base):
    """A value for a project or shipment field that the owner has not chosen: kept here rather than
    written over theirs, until they accept or dismiss it."""

    # Server defaults (created_at, updated_at) come back with the write, not by a lazy load.
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "field_proposals"
    __table_args__ = (
        # A newer proposal for the same field replaces the open one.
        Index(
            "field_proposals_one_open",
            "company_id",
            "target",
            "target_id",
            "field",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index(None, "company_id", "status"),
        CheckConstraint(
            "(status = 'open') = (decided_at IS NULL)", name="field_proposals_decided_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    target: Mapped[ProposalTarget] = mapped_column(
        text_enum(ProposalTarget, "field_proposals_target_check")
    )
    target_id: Mapped[uuid.UUID]
    field: Mapped[str] = mapped_column(String(60))
    value: Mapped[Any] = mapped_column(JSONB)
    source: Mapped[FieldSource] = mapped_column(
        text_enum(FieldSource, "field_proposals_source_check")
    )
    evidence: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, server_default=text("'{}'")
    )
    status: Mapped[ProposalStatus] = mapped_column(
        text_enum(ProposalStatus, "field_proposals_status_check"), default=ProposalStatus.OPEN
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChecklistMarkKind(StrEnum):
    """The owner's word on one item of a project's or a truck's checklist."""

    # Not asked of this job: a domestic job's CMR.
    NOT_NEEDED = "not_needed"
    # Asked of this job though the template does not ask it.
    NEEDED = "needed"
    # Done without a paper Tamga holds: an order given by phone. Never a proof of delivery.
    DONE = "done"


class ChecklistMark(CompanyScoped, Base):
    """The owner's mark on one checklist item: a project's when ``shipment_id`` is null, else one
    truck's. What the item is comes from the template a pack registered; the mark only overrides
    whether it is asked and whether it is done."""

    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "checklist_marks"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "project_id", "shipment_id", "key", postgresql_nulls_not_distinct=True
        ),
        ForeignKeyConstraint(
            ["company_id", "project_id"], ["projects.company_id", "projects.id"], ondelete="CASCADE"
        ),
        # The logistics pack's table, named in the database only, as for project_links.
        ForeignKeyConstraint(
            ["company_id", "project_id", "shipment_id"],
            ["shipments.company_id", "shipments.project_id", "shipments.id"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID]
    shipment_id: Mapped[uuid.UUID | None]
    key: Mapped[str] = mapped_column(String(40))
    mark: Mapped[ChecklistMarkKind] = mapped_column(
        text_enum(ChecklistMarkKind, "checklist_marks_mark_check")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
