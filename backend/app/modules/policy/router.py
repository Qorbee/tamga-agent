from fastapi import APIRouter

from app.modules.identity.deps import ActorDep
from app.modules.policy import service
from app.modules.policy.schemas import PolicyIn, PolicyOut
from app.platform.db import SessionDep

policy = APIRouter(tags=["policy"])


@policy.get("/policy")
async def get_policy(actor: ActorDep, db: SessionDep) -> PolicyOut:
    """The company's policy in force; version 0 is the defaults, while the owner has set none."""
    return await service.get_policy(db, actor)


@policy.put("/policy")
async def set_policy(body: PolicyIn, actor: ActorDep, db: SessionDep) -> PolicyOut:
    """A new version of the policy, in force from now: the owner's only."""
    return await service.set_policy(db, actor, body)
