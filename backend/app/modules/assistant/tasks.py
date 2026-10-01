import logging

from app.modules.assistant import turns
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("assistant.speak", cron="* * * * *")
async def speak(timestamp: int) -> None:
    """Tamga writes first about what waits for the owner and was not written about yet, within
    each company's budget (TAMGA_REQUESTS_PER_DAY)."""
    written = await turns.speak_pending()
    if written:
        logger.info("wrote first in %d chats", written)
