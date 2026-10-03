import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settlement.models import PayeeWallet, VaultAccount


async def get_account(
    db: AsyncSession, company_id: uuid.UUID, *, for_update: bool = False
) -> VaultAccount | None:
    query = select(VaultAccount).where(VaultAccount.company_id == company_id)
    if for_update:
        query = query.with_for_update()
    account: VaultAccount | None = await db.scalar(query)
    return account


async def add(db: AsyncSession, row: VaultAccount | PayeeWallet) -> None:
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
