import logging

from app.packs.logistics import filing
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("logistics.file", cron="*/10 * * * *")
async def file_projects(timestamp: int) -> None:
    """Every ten minutes: a truck paper or a bill that came in is on its truck within the hour."""
    filed = await filing.file_all()
    if filed.trucks or filed.placed or filed.bills:
        logger.info(
            "projects filed: %d trucks proposed, %d papers placed on a truck, %d bills by plate",
            filed.trucks,
            filed.placed,
            filed.bills,
        )
