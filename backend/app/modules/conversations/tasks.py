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
