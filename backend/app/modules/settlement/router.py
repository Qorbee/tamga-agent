import uuid

from fastapi import APIRouter

from app.modules.identity.deps import ActorDep
from app.modules.settlement import service
from app.modules.settlement.schemas import PaymentPage
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

payments = APIRouter(tags=["payments"])


@payments.get("/payments")
async def list_payments(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    decision_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
) -> PaymentPage:
    """The executor's releases from the company's vault, the latest first, each with its
    transaction on the explorer once final."""
    return await service.list_payments(
        db, actor, decision_id=decision_id, invoice_id=invoice_id, page=page
    )
