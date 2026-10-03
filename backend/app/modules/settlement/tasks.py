import uuid

from app.modules.settlement import service
from app.platform import jobs


@jobs.task("settlement.open_account", retry=5)
async def open_account(company_id: str) -> None:
    """A company's account on the vault: its wallets, its registration, its deposit. Tried
    again, it goes on from the step it reached."""
    await service.open_account(uuid.UUID(company_id))
