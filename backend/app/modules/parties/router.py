import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.identity.deps import ActorDep
from app.modules.parties import service
from app.modules.parties.models import PartyKind
from app.modules.parties.schemas import (
    BalanceSide,
    PartyChange,
    PartyGroupIn,
    PartyGroupOut,
    PartyGroupPage,
    PartyOut,
    PartyPage,
    PartySort,
)
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


@parties.get("/party-groups")
async def list_groups(actor: ActorDep, db: SessionDep, page: PageDep) -> PartyGroupPage:
    return await service.list_groups(db, actor, page)


@parties.post("/party-groups", status_code=201)
async def add_group(actor: ActorDep, db: SessionDep, body: PartyGroupIn) -> PartyGroupOut:
    return await service.save_group(db, actor, body)


@parties.put("/party-groups/{group_id}")
async def save_group(
    actor: ActorDep, db: SessionDep, group_id: uuid.UUID, body: PartyGroupIn
) -> PartyGroupOut:
    return await service.save_group(db, actor, body, group_id)


@parties.put("/party-groups/{group_id}/members/{party_id}")
async def add_member(
    actor: ActorDep, db: SessionDep, group_id: uuid.UUID, party_id: uuid.UUID
) -> PartyGroupOut:
    """Puts one firm in the group, leaving its other members as they are now."""
    return await service.change_member(db, actor, group_id, party_id, inside=True)


@parties.delete("/party-groups/{group_id}/members/{party_id}")
async def remove_member(
    actor: ActorDep, db: SessionDep, group_id: uuid.UUID, party_id: uuid.UUID
) -> PartyGroupOut:
    """Takes one firm out of the group, leaving the others as they are now."""
    return await service.change_member(db, actor, group_id, party_id, inside=False)


@parties.delete("/party-groups/{group_id}", status_code=204)
async def delete_group(actor: ActorDep, db: SessionDep, group_id: uuid.UUID) -> None:
    await service.delete_group(db, actor, group_id)
