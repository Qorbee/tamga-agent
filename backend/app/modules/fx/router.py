from fastapi import APIRouter

from app.modules.fx import service
from app.modules.identity.deps import ActorDep
from app.platform.db import SessionDep

fx = APIRouter(tags=["fx"])


@fx.get("/reference-currencies")
async def reference_currencies(actor: ActorDep, db: SessionDep) -> list[str]:
    """The currencies the company's official reference rates convert, the base first."""
    return await service.reference_currencies(db, actor)
