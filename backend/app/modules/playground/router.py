from fastapi import APIRouter, status

from app.modules.identity.deps import ActorDep
from app.modules.playground import service
from app.modules.playground.schemas import Said, ScenarioOut, Skip, WorldOut
from app.platform.db import SessionDep

playground = APIRouter(tags=["playground"])


@playground.get("/playground/scenarios")
async def list_scenarios() -> list[ScenarioOut]:
    """What a judge can open a sandbox with; asked before anyone signs in."""
    return service.list_scenarios()


@playground.get("/playground/world")
async def get_world(actor: ActorDep, db: SessionDep) -> WorldOut:
    return await service.get_world(db, actor)


@playground.post("/playground/world/skip")
async def skip(body: Skip, actor: ActorDep, db: SessionDep) -> WorldOut:
    return await service.skip(db, actor, body.days)


@playground.post("/playground/world/messages", status_code=status.HTTP_204_NO_CONTENT)
async def say_as(body: Said, actor: ActorDep, db: SessionDep) -> None:
    await service.say_as(db, actor, body)
