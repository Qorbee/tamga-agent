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


@jobs.task("documents.forget_file", retry=5)
async def forget_file(company_id: str, sha256: str, storage_key: str) -> None:
    """A deleted document's stored bytes, removed once its deletion has committed."""
    await service.forget_file(uuid.UUID(company_id), bytes.fromhex(sha256), storage_key)
