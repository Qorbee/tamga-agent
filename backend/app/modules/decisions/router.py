import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.modules.decisions import runs, service
from app.modules.decisions.models import DecisionState
from app.modules.decisions.schemas import (
    DecisionAction,
    DecisionChainOut,
    DecisionEventPage,
    DecisionOut,
    DecisionPage,
    DecisionSort,
    EventSort,
    PaymentRunOut,
    RunApprove,
    RunApproved,
)
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

decisions = APIRouter(tags=["decisions"])


@decisions.get("/decisions")
async def list_decisions(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    state: Annotated[list[DecisionState] | None, Query()] = None,
    invoice_id: uuid.UUID | None = None,
    party_id: uuid.UUID | None = None,
    expand: Annotated[list[Literal["invoice"]], Query()] = [],  # noqa: B006
    sort: DecisionSort = "-evaluated_at",
) -> DecisionPage:
    """Decisions about the company's payables, by default the latest evaluated first."""
    return await service.list_decisions(
        db,
        actor,
        states=state,
        invoice_id=invoice_id,
        party_id=party_id,
        with_invoices="invoice" in expand,
        sort=sort,
        page=page,
    )


@decisions.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: uuid.UUID,
    actor: ActorDep,
    db: SessionDep,
    expand: Annotated[list[Literal["invoice"]], Query()] = [],  # noqa: B006
) -> DecisionOut:
    return await service.get_decision(db, actor, decision_id, with_invoice="invoice" in expand)


@decisions.patch("/decisions/{decision_id}")
async def act_on_decision(
    decision_id: uuid.UUID, body: DecisionAction, actor: ActorDep, db: SessionDep
) -> DecisionOut:
    """The owner's answer to the evaluation it names: 409 when it was evaluated again since, or
    when a rule does not allow it."""
    return await service.act(db, actor, decision_id, body)


@decisions.get("/decision-events")
async def list_decision_events(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    decision_id: uuid.UUID | None = None,
    sort: EventSort = "seq",
    expand: Annotated[list[Literal["invoice"]], Query()] = [],  # noqa: B006
) -> DecisionEventPage:
    """The company's decision log, by its place in the chain, each row with its hash and the one
    before it."""
    return await service.list_events(
        db,
        actor,
        decision_id=decision_id,
        newest_first=sort == "-seq",
        with_invoices="invoice" in expand,
        page=page,
    )


@decisions.get("/decision-chain")
async def get_decision_chain(actor: ActorDep, db: SessionDep) -> DecisionChainOut:
    """Whether the company's decision log still holds: each row's hash checked against what the
    row says and the row before it."""
    return await service.chain_state(db, actor)


payment_runs = APIRouter(tags=["payment-runs"])


@payment_runs.get("/payment-runs/current")
async def get_payment_run(actor: ActorDep, db: SessionDep, on: date | None = None) -> PaymentRunOut:
    """The run for a day, by default the next pay day (today without one): what is due by the
    next run, in the order it is weighed, and what the reserve leaves to pay it with."""
    return await runs.payment_run(db, actor, on)


@payment_runs.post("/payment-runs/approvals")
async def approve_run(body: RunApprove, actor: ActorDep, db: SessionDep) -> RunApproved:
    return await runs.approve(db, actor, body)
