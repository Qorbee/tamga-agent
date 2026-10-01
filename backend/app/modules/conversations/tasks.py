import logging

from app.modules.conversations import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("mail.poll", cron="*/2 * * * *")
async def poll_mail(timestamp: int) -> None:
    """New mail within two minutes; a run still going when the next is due absorbs it."""
    stored = await service.poll_pilot_mailboxes()
    if stored:
        logger.info("mail poll stored %d new messages", stored)


@jobs.periodic("mail.process", cron="* * * * *")
async def process_mail(timestamp: int) -> None:
    """Stored mail read within a minute or two: its own text and the facts code finds in it."""
    processed = await service.process_pending()
    if processed:
        logger.info("processed %d messages", processed)


@jobs.periodic("mail.sort", cron="* * * * *")
async def sort_mail(timestamp: int) -> None:
    """Conversations sorted into work, automated and noise: by code, then by the model within the
    company's daily budget."""
    sorted_count = await service.sort_pending()
    if sorted_count:
        logger.info("sorted %d conversations", sorted_count)
