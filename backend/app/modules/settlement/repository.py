import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settlement.models import PayeeWallet, Payment, VaultAccount
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
    db: AsyncSession, company_id: uuid.UUID, decision_id: uuid.UUID, evaluation: int
) -> Payment | None:
    payment: Payment | None = await db.scalar(
        select(Payment)
        .where(
            Payment.company_id == company_id,
            Payment.decision_id == decision_id,
            Payment.evaluation == evaluation,
        )
        .with_for_update()
    )
    return payment


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
