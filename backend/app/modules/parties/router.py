from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.identity.deps import ActorDep
from app.modules.parties import service
from app.modules.parties.models import PartyKind
from app.modules.parties.schemas import PartyPage, PartySort
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
    sort: PartySort = "source",
) -> PartyPage:
    return await service.list_parties(db, actor, kind=kind, q=q, sort=sort, page=page)
