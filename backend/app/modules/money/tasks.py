import logging

from app.modules.money import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("money.sync", cron="*/15 * * * *")
async def sync(timestamp: int) -> None:
    """Every company's estimate from the books and vault account, read again."""
    done = await service.sync_all()
    if done:
        logger.info("read the money of %d companies", done)
