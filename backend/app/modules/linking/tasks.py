import logging
import uuid

from app.modules.linking import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("linking.place", cron="* * * * *")
async def place_conversations(timestamp: int) -> None:
    """Conversations placed among the projects: by code, then by the model within the company's
    daily budget."""
    placed = await service.place_pending()
    if placed:
        logger.info("placed %d conversations", placed)


@jobs.task("linking.name_project", retry=1)
async def name_project(company_id: str, project_id: str) -> None:
    """A project named again by code once a conversation says who or what its job is."""
    await service.name_project(uuid.UUID(company_id), uuid.UUID(project_id))
