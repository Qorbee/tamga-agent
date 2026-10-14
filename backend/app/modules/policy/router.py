import uuid

from fastapi import APIRouter

from app.modules.identity.deps import ActorDep
from app.modules.policy import service
from app.modules.policy.reserve import ReserveRule
from app.modules.policy.schemas import PolicyChange, PolicyDraftOut, PolicyIn, PolicyOut
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


@policy.patch("/policy")
async def change_policy(body: PolicyChange, actor: ActorDep, db: SessionDep) -> PolicyOut:
    """A new version of the policy with only the fields given changed from the one in force: the
    owner's only."""
    return await service.change_policy(db, actor, body)


@policy.get("/policy/reserve-presets")
async def reserve_presets(actor: ActorDep) -> list[ReserveRule]:
    """The reserve rules every company can pick, each one a start for a rule of the owner's own."""
    return service.reserve_presets()


@policy.get("/policy/drafts/{draft_id}")
async def get_policy_draft(draft_id: uuid.UUID, actor: ActorDep, db: SessionDep) -> PolicyDraftOut:
    """A change to the policy drafted in a chat, and whether the owner answered it."""
    return await service.get_draft(db, actor, draft_id)


@policy.post("/policy/drafts/{draft_id}/confirm")
async def confirm_policy_draft(
    draft_id: uuid.UUID, actor: ActorDep, db: SessionDep
) -> PolicyDraftOut:
    """Puts the drafted change in force over the version in force now: the owner's only."""
    return await service.confirm_draft(db, actor, draft_id)


@policy.post("/policy/drafts/{draft_id}/discard")
async def discard_policy_draft(
    draft_id: uuid.UUID, actor: ActorDep, db: SessionDep
) -> PolicyDraftOut:
    """Drops the drafted change: the owner's only."""
    return await service.discard_draft(db, actor, draft_id)
