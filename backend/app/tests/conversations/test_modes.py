from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import Company, CompanyKind, CompanyMode
from app.modules.conversations.adapters import ADAPTERS
from app.modules.conversations.adapters.base import ModeForbidsSending, Outbound

pytestmark = pytest.mark.integration

OUTBOUND = Outbound(to="client@shipper.example", text="Ödeme ne zaman?")


async def test_no_adapter_sends_for_a_shadow_or_replay_company(db: AsyncSession) -> None:
    # Root AGENTS.md invariant 6: the refusal is in code, before any adapter's own delivery.
    shadow = await companies.create_company(db, name="Pilot", kind=CompanyKind.PILOT)
    replay = Company(
        name="Pilot replay",
        kind=CompanyKind.PILOT,
        mode=CompanyMode.REPLAY,
        replay_cursor=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db.add(replay)
    await db.commit()

    for adapter in ADAPTERS.values():
        for company in (shadow, replay):
            with pytest.raises(ModeForbidsSending):
                await adapter.send(db, company.id, OUTBOUND)


async def test_a_live_company_reaches_the_adapter(db: AsyncSession) -> None:
    sandbox = await companies.create_company(db, name="Sandbox", kind=CompanyKind.SANDBOX)
    await db.commit()

    # Mail is read-only, so the email adapter itself declines: the mode check let it through.
    with pytest.raises(NotImplementedError):
        await ADAPTERS[next(iter(ADAPTERS))].send(db, sandbox.id, OUTBOUND)
