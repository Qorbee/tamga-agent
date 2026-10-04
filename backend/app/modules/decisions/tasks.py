import logging

from app.modules.decisions import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("decisions.evaluate", cron="*/5 * * * *")
async def evaluate(timestamp: int) -> None:
    """Every payable evaluated again within minutes of what is known of it changing; one whose
    picture did not change keeps its evaluation."""
    moved = await service.evaluate_pending()
    if moved:
        logger.info("%d decisions were made or moved", moved)
