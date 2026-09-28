import logging

from app.modules.identity import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("sandboxes.expire", cron="*/15 * * * *")
async def expire_sandboxes(timestamp: int) -> None:
    """A sandbox lives GUEST_HOURS; within fifteen minutes after, it and its files are gone."""
    deleted = await service.expire_sandboxes()
    if deleted:
        logger.info("deleted %d expired sandboxes", deleted)
