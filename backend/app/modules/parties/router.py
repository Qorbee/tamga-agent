import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.identity.deps import ActorDep
from app.modules.parties import service
from app.modules.parties.models import PartyKind
from app.modules.parties.schemas import BalanceSide, PartyChange, PartyOut, PartyPage, PartySort
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

parties = APIRouter(tags=["parties"])


@parties.get("/parties")
async def list_parties(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    kind: PartyKind | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    balance_side: Annotated[
        BalanceSide | None,
        Query(description="Only parties whose balance in the books runs this way."),
    ] = None,
    sort: PartySort = "source",
) -> PartyPage:
    return await service.list_parties(
        db, actor, kind=kind, q=q, balance_side=balance_side, sort=sort, page=page
    )


@parties.get("/parties/{party_id}")
async def get_party(actor: ActorDep, db: SessionDep, party_id: uuid.UUID) -> PartyOut:
    return await service.get_party(db, actor, party_id)


@parties.patch("/parties/{party_id}")
async def change_party(
    actor: ActorDep, db: SessionDep, party_id: uuid.UUID, body: PartyChange
) -> PartyOut:
    return await service.change_party(db, actor, party_id, body)
