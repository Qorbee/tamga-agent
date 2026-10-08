import logging

from app.modules.assistant import context, turns
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("assistant.speak", cron="* * * * *")
async def speak(timestamp: int) -> None:
    """Tamga writes first about what waits for the owner and was not written about yet, within
    each company's budget (TAMGA_REQUESTS_PER_DAY)."""
    written = await turns.speak_pending()
    if written:
        logger.info("wrote first in %d chats", written)


@jobs.periodic("assistant.summarize", cron="*/5 * * * *")
async def summarize(timestamp: int) -> None:
    """A chat gone quiet gets a summary that later chats' searches find (ADR-0025)."""
    written = await context.summarize_quiet()
    if written:
        logger.info("summarized %d chats", written)
