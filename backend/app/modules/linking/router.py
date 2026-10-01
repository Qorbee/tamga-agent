import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.identity.deps import ActorDep
from app.modules.linking import service
from app.modules.linking.models import Outcome
from app.modules.linking.schemas import PlacementDecision, PlacementOut, PlacementPage
from app.platform.db import SessionDep
from app.platform.pagination import MAX_LIMIT, PageDep

placements = APIRouter(tags=["placements"])


@placements.get("/placements")
async def list_placements(
    actor: ActorDep,
    db: SessionDep,
    page: PageDep,
    outcome: Annotated[list[Outcome], Query()] = [],  # noqa: B006
    project_id: Annotated[list[uuid.UUID], Query(max_length=MAX_LIMIT)] = [],  # noqa: B006
) -> PlacementPage:
    return await service.list_placements(
        db, actor, outcomes=outcome, project_ids=project_id, page=page
    )


@placements.patch("/placements/{placement_id}")
async def decide_placement(
    placement_id: uuid.UUID, body: PlacementDecision, actor: ActorDep, db: SessionDep
) -> PlacementOut:
    return await service.decide_placement(
        db,
        actor,
        placement_id,
        confirm=None if body.links is None else body.links == "confirmed",
        project_id=body.project_id,
        moving_from=body.from_project_id,
    )
