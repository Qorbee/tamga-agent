import logging
import uuid

from app.modules.playground import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.task("playground.seed", retry=2)
async def seed(company_id: str) -> None:
    """A new sandbox, filled with the scenario its judge picked."""
    await service.seed(uuid.UUID(company_id))


@jobs.task("playground.play", retry=2)
async def play(company_id: str) -> None:
    """The steps of a sandbox's script its clock has passed."""
    await service.play_due(uuid.UUID(company_id))


@jobs.periodic("playground.play_due", cron="* * * * *")
async def play_due(timestamp: int) -> None:
    """Steps the wall clock reaches in a sandbox nobody skipped."""
    queued = await service.play_due_everywhere()
    if queued:
        logger.info("queued the scripts of %d sandboxes", queued)
