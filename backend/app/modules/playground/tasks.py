import uuid

from app.modules.playground import service
from app.platform import jobs


@jobs.task("playground.seed", retry=2)
async def seed(company_id: str) -> None:
    """A new sandbox, filled with what the registered scenarios hold."""
    await service.seed(uuid.UUID(company_id))
