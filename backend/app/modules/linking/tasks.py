import logging

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
