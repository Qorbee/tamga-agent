import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies.models import CompanyKind
from app.modules.identity import service as identity
from app.routers import traction

pytestmark = pytest.mark.integration


async def test_the_figures_count_real_companies_only(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    traction._kept.clear()
    await identity.bootstrap_company(
        db, name="Acme Lojistik", kind=CompanyKind.PILOT, owner_email="owner@example.com"
    )
    await client.post("/sessions/guest")  # a sandbox, which is no real company

    figures = (await client.get("/traction")).json()

    assert figures["companies"] == 1
    assert set(figures) == {
        "companies",
        "messages",
        "files",
        "invoices",
        "projects",
        "bills_evaluated",
        "bills_held",
        "mirrored",
    }
    assert all(isinstance(value, int) for value in figures.values())
