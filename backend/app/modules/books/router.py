import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.books import service
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.schemas import InvoiceOut, InvoicePage, InvoiceSort
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
    kind: Annotated[list[InvoiceKind] | None, Query()] = None,
    party_id: uuid.UUID | None = None,
    outstanding: Annotated[
        bool | None,
        Query(
            description=(
                "True: not cancelled and something left to pay. False: cancelled, or nothing "
                "left. One the books give no amount left for is in neither."
            )
        ),
    ] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: InvoiceSort = "-issue_date",
) -> InvoicePage:
    return await service.list_invoices(
        db,
        actor,
        direction=direction,
        kinds=kind,
        party_id=party_id,
        outstanding=outstanding,
        q=q,
        sort=sort,
        page=page,
    )


@invoices.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> InvoiceOut:
    return await service.get_invoice(db, actor, invoice_id)
