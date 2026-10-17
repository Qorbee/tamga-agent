import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.policy.models import PaymentMethod
from app.platform.db import Base, text_enum
from app.platform.tenancy import CompanyScoped


class DecisionState(StrEnum):
    # Every rule passed or left it to the owner: it waits for an approval.
    AWAITING_OWNER = "awaiting_owner"
    # A rule holds it, or the owner did; ``decided_by`` says which.
    HELD = "held"
    # A rule rejects it (a bill already paid), or the owner did.
    REJECTED = "rejected"
    # A rule holds it because the books show it paid: the invoice itself, or, for a company that
    # records payments on account, the firm's account (ADR-0022). Nothing waits on anyone.
    PAID_IN_BOOKS = "paid_in_books"
    # The owner approved the evaluation it names: the executor pays it from the vault, or the
    # owner pays it in the bank, which a matched movement or the owner's word settles.
    APPROVED = "approved"
    # Paid; its payment holds the proof: the transaction's hash, the bank's movement, or the
    # owner who marked it paid.
    SETTLED = "settled"
    # The vault refused it; ``decision_log`` holds the reason. With ``decided_by`` kept, for a
    # reason another evaluation will not change; without, it is evaluated again.
    FAILED = "failed"


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
    # The official reference rate the amount was compared with the policy's limits at, the day it
    # was published for and its source (ADR-0028); None when the bill is in the policy's
    # currency, or no rate stands.
    reference_rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    reference_rate_on: Mapped[date | None]
    reference_source: Mapped[str | None] = mapped_column(String(20))
    # The owner set the amount (a part payment): kept through later evaluations.
    amount_edited: Mapped[bool] = mapped_column(default=False)
    # How it is paid (ADR-0027): proposed by code from the policy and the payee, unless the owner
    # chose it, with the pay currency, which is then kept through later evaluations.
    method: Mapped[PaymentMethod] = mapped_column(
        text_enum(PaymentMethod, "decisions_method_check", length=20),
        server_default=PaymentMethod.VAULT_USDC.value,
    )
    pay_chosen: Mapped[bool] = mapped_column(default=False, server_default="false")
    # What leaves the company's account: in the bill's currency, another the owner pays from, or
    # test USDC; the amount expected at the reference rate of the evaluation's day, its rate,
    # day and source. None where no rate stands.
    pay_currency: Mapped[str | None] = mapped_column(String(4))
    pay_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    pay_rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    pay_rate_on: Mapped[date | None]
    pay_rate_source: Mapped[str | None] = mapped_column(String(20))
    # Each rule's result: ``{"rule", "outcome", "reason"}``, in the rules' order.
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # The bill's issue day as the books gave it at the last evaluation, which lists are sorted by.
    issued_on: Mapped[date | None]
    # The controller's latest proposal for this evaluation (``agents.Advice`` with its
    # evaluation, inputs, model and trace), an opinion that moves nothing; None until one is in,
    # and again on the next evaluation.
    advice: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The owner who approved, held or rejected it; None while a rule's result stands.
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    evaluated_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LogEvent(StrEnum):
    PROPOSED = "proposed"
    # The controller's opinion on a proposal that waits for the owner.
    ADVISED = "advised"
    EDITED = "edited"
    # The owner chose another way to pay it, or another currency to pay it from.
    PAY_BY = "pay_by"
    APPROVED = "approved"
    HELD = "held"
    REJECTED = "rejected"
    # Paid, with its proof: the executor's transaction, the bank movement matched to it, or the
    # owner's word; refused, with the reason; and what a replay company would have released,
    # never sent (ADR-0009).
    SETTLED = "settled"
    FAILED = "failed"
    WOULD_RELEASE = "would_release"
    # The owner said a bank transfer settled by mistake was not paid: back to approved, with
    # their reason, the way they correct Tamga's match or their own word.
    UNPAID = "unpaid"
    # In shadow, the test USDC released beside a payment made by bank, with its transaction: a
    # record of the bank's payment on Arc, never the bill's payment (ADR-0027).
    MIRRORED = "mirrored"


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


class AdviceRequest(CompanyScoped, Base):
    """One time the controller was asked about an evaluation, written before the call is sent: what
    the day's budget counts, whatever came back, and what keeps a call that failed from being
    sent again for the same inputs."""

    __tablename__ = "advice_requests"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "decision_id"],
            ["decisions.company_id", "decisions.id"],
            ondelete="CASCADE",
        ),
        Index(None, "company_id", "at"),
        Index(None, "company_id", "decision_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    decision_id: Mapped[uuid.UUID]
    evaluation: Mapped[int]
    # The SHA-256 of what the controller was shown, but the day.
    inputs: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime]


class AnchorState(StrEnum):
    # Its idempotency key is written; the executor's call is on its way, or is sent again with
    # the same key.
    SUBMITTING = "submitting"
    SETTLED = "settled"
    # The vault refused it; ``failure`` says why. The next batch anchors the heads again.
    FAILED = "failed"


class DecisionAnchor(Base):
    """One root over the companies' decision-log heads, put on the chain by the executor
    (05, The log; ADR-0015). Global (ADR-0003): it keeps every head it covers, so a company's
    log is checked against it and the root can be computed again from it."""

    __tablename__ = "decision_anchors"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    root: Mapped[str] = mapped_column(String(64))
    # Each company's head as anchored: ``{company_id: {"seq": n, "hash": "…"}}``.
    heads: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSONB)
    state: Mapped[AnchorState] = mapped_column(
        text_enum(AnchorState, "decision_anchors_state_check", length=20)
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    circle_transaction_id: Mapped[str | None] = mapped_column(String(100))
    tx_hash: Mapped[str | None] = mapped_column(String(66))
    failure: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
