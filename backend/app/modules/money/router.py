import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.modules.identity.deps import ActorDep
from app.modules.money import service
from app.modules.money.models import AccountKind, MovementCategory
from app.modules.money.schemas import (
    AccountChange,
    AccountIn,
    AccountOut,
    AccountPage,
    Holdings,
    MovementChange,
    MovementIn,
    MovementOut,
    MovementPage,
    MovementSort,
)
from app.platform.db import SessionDep
from app.platform.pagination import PageDep

money = APIRouter(tags=["money"])


@money.get("/money/accounts")
async def list_accounts(
    actor: ActorDep,
    db: SessionDep,
    kind: Annotated[list[AccountKind] | None, Query()] = None,
) -> AccountPage:
    """The company's accounts: banks, cash boxes, the vault, and the books' estimate."""
    return await service.list_accounts(db, actor, kinds=kind)


@money.post("/money/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountIn, actor: ActorDep, db: SessionDep) -> AccountOut:
    return await service.create_account(db, actor, body)


@money.patch("/money/accounts/{account_id}")
async def change_account(
    account_id: uuid.UUID, body: AccountChange, actor: ActorDep, db: SessionDep
) -> AccountOut:
    """An account the owner keeps; 409 for one a source reads."""
    return await service.change_account(db, actor, account_id, body)


@money.get("/money/movements")
async def list_movements(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    account_id: uuid.UUID | None = None,
    category: Annotated[list[MovementCategory] | None, Query()] = None,
    uncategorised: Annotated[bool, Query(description="Only those with no category.")] = False,
    since: date | None = None,
    until: date | None = None,
    estimated: Annotated[
        bool | None, Query(description="True: estimates from the books; false: none of them.")
    ] = None,
    sort: MovementSort = "-booked_on",
) -> MovementPage:
    return await service.list_movements(
        db,
        actor,
        account_id=account_id,
        categories=category,
        uncategorised=uncategorised,
        since=since,
        until=until,
        estimated=estimated,
        sort=sort,
        page=page,
    )


@money.post("/money/movements", status_code=status.HTTP_201_CREATED)
async def add_movement(body: MovementIn, actor: ActorDep, db: SessionDep) -> MovementOut:
    """A movement on an account the owner keeps; 409 for one a source reads."""
    return await service.add_movement(db, actor, body)


@money.patch("/money/movements/{movement_id}")
async def change_movement(
    movement_id: uuid.UUID, body: MovementChange, actor: ActorDep, db: SessionDep
) -> MovementOut:
    return await service.change_movement(db, actor, movement_id, body)


@money.get("/money/holdings")
async def holdings(
    actor: ActorDep,
    db: SessionDep,
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")],
) -> Holdings:
    """What the bank and cash accounts hold, and their total in ``currency`` at the reference
    rate of the company's day, each rate with its day and source."""
    return await service.holdings(db, actor, currency)
