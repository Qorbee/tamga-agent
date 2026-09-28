import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.companies import service as companies
from app.modules.companies.models import CompanyKind
from app.modules.facts import service
from app.modules.facts.service import NewFact
from app.platform.items import ItemKind

pytestmark = pytest.mark.integration


async def test_reading_an_item_again_keeps_one_fact_at_its_best_confidence(
    db: AsyncSession,
) -> None:
    company = await companies.create_company(db, name="Acme", kind=CompanyKind.PILOT)
    mail = uuid.uuid4()
    plate = NewFact("plate", "34 ABC 12", "34ABC12", 0.6, "model:facts")

    [first] = await service.record_facts(
        db, company_id=company.id, item_kind=ItemKind.MESSAGE, item_id=mail, facts=[plate]
    )
    [again] = await service.record_facts(
        db,
        company_id=company.id,
        item_kind=ItemKind.MESSAGE,
        item_id=mail,
        facts=[NewFact("plate", "34ABC12", "34ABC12", 0.95, "regex")],
    )

    assert again.id == first.id and again.confidence == 0.95 and again.extractor == "regex"
    [shared] = await service.items_sharing(
        db, company_id=company.id, kind="plate", normalised="34ABC12"
    )
    assert shared.item_id == mail
