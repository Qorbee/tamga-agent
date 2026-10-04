import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.decisions.models import Decision, DecisionEvent, DecisionState, LogEvent
from app.platform.pagination import PageParams


async def lock_decisions(db: AsyncSession, company_id: uuid.UUID) -> None:
    """The company's decisions and its log are written one transaction at a time: an evaluation
    and an owner's answer never cross, and the log's chain has one end."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"decisions:{company_id}"},
    )


async def add(db: AsyncSession, row: Decision | DecisionEvent) -> None:
    db.add(row)
    await db.flush()


async def get(db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID) -> Decision | None:
    decision: Decision | None = await db.scalar(
        select(Decision).where(Decision.company_id == company_id, Decision.id == decision_id)
    )
    return decision


async def for_invoice(
    db: AsyncSession, company_id: uuid.UUID, invoice_id: uuid.UUID
) -> Decision | None:
    decision: Decision | None = await db.scalar(
        select(Decision).where(Decision.company_id == company_id, Decision.invoice_id == invoice_id)
    )
    return decision


# Approved, a decision is the executor's; paid, it is done.
_EXECUTORS = (DecisionState.APPROVED, DecisionState.SETTLED)


def _refused_for_good() -> ColumnElement[bool]:
    """Refused by the vault for a reason no evaluation changes (a key it had paid among them):
    the owner's approval is kept, for someone to look at."""
    return (Decision.state == DecisionState.FAILED) & Decision.decided_by.is_not(None)


def _movable() -> ColumnElement[bool]:
    """Decisions an evaluation may change: no owner answered them, and none is approved or
    paid."""
    return Decision.decided_by.is_(None) & Decision.state.not_in(_EXECUTORS)


async def movable_invoices(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Decision.invoice_id).where(Decision.company_id == company_id, _movable())
    )
    return list(rows)


async def companies_with_movable(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(select(Decision.company_id).where(_movable()).distinct())
    return list(rows)


async def awaiting_owner(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Decision]:
    rows = await db.scalars(
        select(Decision)
        .where(Decision.company_id == company_id, Decision.state == DecisionState.AWAITING_OWNER)
        .order_by(Decision.evaluated_at, Decision.id)
    )
    return rows.all()


async def companies_awaiting_owner(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Decision.company_id).where(Decision.state == DecisionState.AWAITING_OWNER).distinct()
    )
    return list(rows)


async def approved(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Decision]:
    rows = await db.scalars(
        select(Decision)
        .where(Decision.company_id == company_id, Decision.state == DecisionState.APPROVED)
        .order_by(Decision.decided_at, Decision.id)
    )
    return rows.all()


async def companies_with_approved(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(Decision.company_id).where(Decision.state == DecisionState.APPROVED).distinct()
    )
    return list(rows)


async def approved_among(
    db: AsyncSession, company_id: uuid.UUID, invoice_ids: Sequence[uuid.UUID]
) -> bool:
    if not invoice_ids:
        return False
    found = await db.scalar(
        select(Decision.id)
        .where(
            Decision.company_id == company_id,
            Decision.invoice_id.in_(invoice_ids),
            Decision.state.in_(_EXECUTORS) | _refused_for_good(),
        )
        .limit(1)
    )
    return found is not None


async def list_decisions(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    states: Sequence[DecisionState] | None,
    invoice_id: uuid.UUID | None,
    party_id: uuid.UUID | None,
    page: PageParams,
) -> tuple[Sequence[Decision], int]:
    filters = [Decision.company_id == company_id]
    if states:
        filters.append(Decision.state.in_(states))
    if invoice_id is not None:
        filters.append(Decision.invoice_id == invoice_id)
    if party_id is not None:
        filters.append(Decision.party_id == party_id)
    total = await db.scalar(select(func.count()).select_from(Decision).where(*filters))
    rows = await db.scalars(
        select(Decision)
        .where(*filters)
        .order_by(Decision.evaluated_at.desc(), Decision.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def last_event(db: AsyncSession, company_id: uuid.UUID) -> DecisionEvent | None:
    event: DecisionEvent | None = await db.scalar(
        select(DecisionEvent)
        .where(DecisionEvent.company_id == company_id)
        .order_by(DecisionEvent.seq.desc())
        .limit(1)
    )
    return event


async def list_events(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    decision_id: uuid.UUID | None,
    newest_first: bool,
    page: PageParams,
) -> tuple[Sequence[DecisionEvent], int]:
    filters = [DecisionEvent.company_id == company_id]
    if decision_id is not None:
        filters.append(DecisionEvent.decision_id == decision_id)
    total = await db.scalar(select(func.count()).select_from(DecisionEvent).where(*filters))
    rows = await db.scalars(
        select(DecisionEvent)
        .where(*filters)
        .order_by(DecisionEvent.seq.desc() if newest_first else DecisionEvent.seq)
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def invoices_of(
    db: AsyncSession, company_id: uuid.UUID, decision_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Each decision's bill."""
    if not decision_ids:
        return {}
    rows = await db.execute(
        select(Decision.id, Decision.invoice_id).where(
            Decision.company_id == company_id, Decision.id.in_(decision_ids)
        )
    )
    return {decision_id: invoice_id for decision_id, invoice_id in rows.tuples()}


async def proposals_of(
    db: AsyncSession, company_id: uuid.UUID, decision_ids: Sequence[uuid.UUID]
) -> Sequence[DecisionEvent]:
    """Every evaluation logged for these decisions."""
    if not decision_ids:
        return []
    rows = await db.scalars(
        select(DecisionEvent).where(
            DecisionEvent.company_id == company_id,
            DecisionEvent.decision_id.in_(decision_ids),
            DecisionEvent.event == LogEvent.PROPOSED,
        )
    )
    return rows.all()


async def chain_of(db: AsyncSession, company_id: uuid.UUID) -> Sequence[DecisionEvent]:
    rows = await db.scalars(
        select(DecisionEvent)
        .where(DecisionEvent.company_id == company_id)
        .order_by(DecisionEvent.seq)
    )
    return rows.all()
