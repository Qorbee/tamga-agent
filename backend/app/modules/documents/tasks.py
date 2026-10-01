import logging
import uuid

from app.modules.documents import service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("documents.read", cron="* * * * *")
async def read_documents(timestamp: int) -> None:
    """A file asked for is read within a minute or two: its text and the facts code finds in it."""
    read = await service.read_pending()
    if read:
        logger.info("read %d documents", read)


@jobs.periodic("documents.read_vision", cron="* * * * *")
async def read_with_vision(timestamp: int) -> None:
    """Photos and scanned pages, read by the vision model within each company's daily budget."""
    read = await service.read_pending_with_vision()
    if read:
        logger.info("the vision model read %d documents", read)


@jobs.task("documents.forget_file", retry=5)
async def forget_file(company_id: str, sha256: str, storage_key: str) -> None:
    """A deleted document's stored bytes, removed once its deletion has committed."""
    await service.forget_file(uuid.UUID(company_id), bytes.fromhex(sha256), storage_key)
