import logging

from app.modules.documents import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("documents.read", cron="* * * * *")
async def read_documents(timestamp: int) -> None:
    """A file asked for is read within a minute or two: its text and the facts code finds in it."""
    read = await service.read_pending()
    if read:
        logger.info("read %d documents", read)
