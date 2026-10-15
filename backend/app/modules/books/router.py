import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.books import service
from app.modules.books.models import InvoiceDirection, InvoiceKind
from app.modules.books.schemas import (
    InvoiceFile,
    InvoiceOut,
    InvoicePage,
    InvoiceSort,
    InvoiceTotal,
)
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

invoices = APIRouter(tags=["invoices"])


def invoice_filter(
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
    due_before: Annotated[date | None, Query(description="Due before this day.")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> service.InvoiceFilter:
    return service.InvoiceFilter(
        direction=direction,
        kinds=kind,
        party_id=party_id,
        outstanding=outstanding,
        due_before=due_before,
        q=q.strip() if q else None,
    )


InvoiceFilterDep = Annotated[service.InvoiceFilter, Depends(invoice_filter)]


@invoices.get("/invoices")
async def list_invoices(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    which: InvoiceFilterDep,
    sort: InvoiceSort = "-issue_date",
) -> InvoicePage:
    return await service.list_invoices(db, actor, which, sort=sort, page=page)


@invoices.get("/invoice-totals")
async def invoice_totals(
    actor: ActorDep, db: SessionDep, which: InvoiceFilterDep
) -> list[InvoiceTotal]:
    """What the invoices ``/invoices`` would list with the same filters add up to, per
    currency."""
    return await service.invoice_totals(db, actor, which)


@invoices.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> InvoiceOut:
    return await service.get_invoice(db, actor, invoice_id)


@invoices.post("/invoices/{invoice_id}/file")
async def invoice_file(invoice_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> InvoiceFile:
    """The document holding the invoice's own file: one its mail carried, else the file the books'
    source renders for its e-document, read once and kept. 404 ``no_invoice_file`` when there is
    none."""
    return await service.invoice_file(db, actor, invoice_id)
