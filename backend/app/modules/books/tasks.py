import logging

from app.modules.books import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("books.sync", cron="17 */6 * * *")
async def sync_books(timestamp: int) -> None:
    """Four times a day: fresh enough for payment decisions, and well inside the read budget."""
    result = await service.sync_pilot_books()
    if result.parties or result.invoices or result.dispatches:
        logger.info(
            "books sync read %d cards, %d invoices, %d dispatches",
            result.parties,
            result.invoices,
            result.dispatches,
        )
