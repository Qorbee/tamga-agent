import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class DecisionState(StrEnum):
    # Every rule passed or left it to the owner: it waits for an approval.
    AWAITING_OWNER = "awaiting_owner"
    # A rule holds it, or the owner did; ``decided_by`` says which.
    HELD = "held"
    # A rule rejects it (a bill already paid), or the owner did.
    REJECTED = "rejected"
    # The owner approved the evaluation it names; the executor pays it.
    APPROVED = "approved"


class Decision(CompanyScoped, Base):
    """What Tamga decided about one payable, as it stands now (05, Data): the amount and its
    test USDC, every rule's result, and the evaluation an approval must name. Its history is
    ``decision_log``."""

    __tablename__ = "decisions"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "invoice_id"),
        UniqueConstraint("company_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    invoice_id: Mapped[uuid.UUID]
    party_id: Mapped[uuid.UUID]
    state: Mapped[DecisionState] = mapped_column(
        text_enum(DecisionState, "decisions_state_check", length=20)
    )
    # Counts the evaluations: each one is a new proposal, and an approval names the one it
    # approves, so one for an amount or a picture since changed is refused.
    evaluation: Mapped[int]
    # The hash of everything the last evaluation read: the next pass decides again only when it
    # changed.
    picture_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[int]
    # What would be paid, in the bill's currency, and as test USDC (ADR-0019).
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    usdc_units: Mapped[int | None] = mapped_column(BigInteger)
    # The owner set the amount (a part payment): kept through later evaluations.
    amount_edited: Mapped[bool] = mapped_column(default=False)
    # Each rule's result: ``{"rule", "outcome", "reason"}``, in the rules' order.
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # The owner who approved, held or rejected it; None while a rule's result stands.
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    evaluated_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LogEvent(StrEnum):
    PROPOSED = "proposed"
    EDITED = "edited"
    APPROVED = "approved"
    HELD = "held"
    REJECTED = "rejected"


class DecisionEvent(CompanyScoped, Base):
    """One event of a decision's history (05, Data), never updated: a later fact is a later row.
    The rows of a company form one chain: each holds the hash of its content and of the row
    before it, so a row changed or removed breaks every hash after it."""

    __tablename__ = "decision_log"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("company_id", "seq"),
        # No cascade: a decision with a history is never deleted on its own, only with its
        # company, whose deletion takes both.
        ForeignKeyConstraint(
            ["company_id", "decision_id"], ["decisions.company_id", "decisions.id"]
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    decision_id: Mapped[uuid.UUID]
    # Its place in the company's chain, from 1.
    seq: Mapped[int]
    event: Mapped[LogEvent] = mapped_column(text_enum(LogEvent, "decision_log_event_check"))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Who acted; None for Tamga's own evaluation.
    user_id: Mapped[uuid.UUID | None]
    at: Mapped[datetime]
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
