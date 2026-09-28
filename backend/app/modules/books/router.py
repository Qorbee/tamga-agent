import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.books import service
from app.modules.books.models import InvoiceDirection
from app.modules.books.schemas import InvoicePage
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

invoices = APIRouter(tags=["invoices"])


@invoices.get("/invoices")
async def list_invoices(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    direction: InvoiceDirection | None = None,
    party_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> InvoicePage:
    return await service.list_invoices(
        db, actor, direction=direction, party_id=party_id, q=q, page=page
    )
