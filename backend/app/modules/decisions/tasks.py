import logging

from app.modules.decisions import anchoring, service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("decisions.evaluate", cron="*/5 * * * *")
async def evaluate(timestamp: int) -> None:
    """Every payable evaluated again within minutes of what is known of it changing; one whose
    picture did not change keeps its evaluation."""
    moved = await service.evaluate_pending()
    if moved:
        logger.info("%d decisions were made or moved", moved)


@jobs.periodic("decisions.anchor", cron="*/15 * * * *")
async def anchor(timestamp: int) -> None:
    """The companies' log heads on the chain, when any moved since the last anchor."""
    if await anchoring.anchor_heads():
        logger.info("the decision log's heads were anchored")
