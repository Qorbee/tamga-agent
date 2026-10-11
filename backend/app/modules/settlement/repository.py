import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settlement.models import (
    AccountState,
    PayeeWallet,
    Payment,
    PaymentKind,
    PaymentState,
    VaultAccount,
)
from app.platform.pagination import PageParams


async def get_account(
    db: AsyncSession, company_id: uuid.UUID, *, for_update: bool = False
) -> VaultAccount | None:
    query = select(VaultAccount).where(VaultAccount.company_id == company_id)
    if for_update:
        query = query.with_for_update()
    account: VaultAccount | None = await db.scalar(query)
    return account


async def add(db: AsyncSession, row: VaultAccount | PayeeWallet | Payment) -> None:
    db.add(row)
    await db.flush()


async def payee_wallets(db: AsyncSession, company_id: uuid.UUID) -> list[PayeeWallet]:
    rows = await db.scalars(
        select(PayeeWallet)
        .where(PayeeWallet.company_id == company_id)
        .order_by(PayeeWallet.created_at, PayeeWallet.id)
    )
    return list(rows)


async def payee_wallets_of(
    db: AsyncSession, company_id: uuid.UUID, party_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, PayeeWallet]:
    if not party_ids:
        return {}
    rows = await db.scalars(
        select(PayeeWallet).where(
            PayeeWallet.company_id == company_id, PayeeWallet.party_id.in_(party_ids)
        )
    )
    return {row.party_id: row for row in rows}


async def payment_for(
    db: AsyncSession,
    company_id: uuid.UUID,
    decision_id: uuid.UUID,
    evaluation: int,
    kind: PaymentKind = PaymentKind.PAYMENT,
) -> Payment | None:
    payment: Payment | None = await db.scalar(
        select(Payment)
        .where(
            Payment.company_id == company_id,
            Payment.decision_id == decision_id,
            Payment.evaluation == evaluation,
            Payment.kind == kind,
        )
        .with_for_update()
    )
    return payment


async def matched_movements(db: AsyncSession, company_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.scalars(
        select(Payment.movement_id).where(
            Payment.company_id == company_id, Payment.movement_id.is_not(None)
        )
    )
    return {row for row in rows if row is not None}


async def list_payments(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    decision_ids: Sequence[uuid.UUID] | None,
    invoice_id: uuid.UUID | None,
    page: PageParams,
) -> tuple[Sequence[Payment], int]:
    filters = [Payment.company_id == company_id]
    if decision_ids:
        filters.append(Payment.decision_id.in_(decision_ids))
    if invoice_id is not None:
        filters.append(Payment.invoice_id == invoice_id)
    total = await db.scalar(select(func.count()).select_from(Payment).where(*filters))
    rows = await db.scalars(
        select(Payment)
        .where(*filters)
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    )
    return rows.all(), total or 0


async def settled_payments(db: AsyncSession, company_id: uuid.UUID) -> Sequence[Payment]:
    rows = await db.scalars(
        select(Payment)
        .where(
            Payment.company_id == company_id,
            Payment.state == PaymentState.SETTLED,
            Payment.usdc_units.is_not(None),
        )
        .order_by(Payment.finished_at, Payment.id)
    )
    return rows.all()


async def companies_with_open_accounts(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.scalars(
        select(VaultAccount.company_id).where(VaultAccount.state == AccountState.OPEN)
    )
    return list(rows)
