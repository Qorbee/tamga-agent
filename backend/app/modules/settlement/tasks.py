import logging
import uuid

from app.modules.settlement import executor, service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.task("settlement.open_account", retry=5)
async def open_account(company_id: str) -> None:
    """A company's account on the vault: its wallets, its registration, its deposit. Tried
    again, it goes on from the step it reached."""
    await service.open_account(uuid.UUID(company_id))


@jobs.periodic("settlement.pay", cron="* * * * *")
async def pay(timestamp: int) -> None:
    """Each decision the owner approved is paid within a minute or two (ADR-0002)."""
    paid = await executor.pay_approved()
    if paid:
        logger.info("paid %d approved decisions", paid)
