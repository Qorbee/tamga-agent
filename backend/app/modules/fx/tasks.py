import logging

from app.modules.fx import service
from app.platform import jobs
from app.platform.clock import utcnow

logger = logging.getLogger(__name__)


@jobs.periodic("fx.refresh", cron="15 * * * *")
async def refresh(timestamp: int) -> None:
    """Hourly: a day stored is never asked for again, so a run costs a request or two, and a
    day published late, or a server just deployed, waits an hour at most."""
    added = await service.refresh(utcnow().date())
    if added:
        logger.info("read %d reference rates", added)
