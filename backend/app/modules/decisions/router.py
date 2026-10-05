import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.modules.decisions import service
from app.modules.decisions.models import DecisionState
from app.modules.decisions.schemas import (
    DecisionAction,
    DecisionChainOut,
    DecisionEventPage,
    DecisionOut,
    DecisionPage,
    DecisionSort,
    EventSort,
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
