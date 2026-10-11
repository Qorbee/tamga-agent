import logging
import uuid

from app.modules.decisions import advising, anchoring, service
from app.platform import jobs

logger = logging.getLogger(__name__)


@jobs.periodic("decisions.evaluate", cron="*/5 * * * *")
async def evaluate(timestamp: int) -> None:
    """Every payable evaluated again within minutes of what is known of it changing; one whose
    picture did not change keeps its evaluation. First, the bills the owner paid by bank whose
    movement came in are settled, so they are no longer counted as owed."""
    settled = await service.settle_matched()
    if settled:
        logger.info("%d bank transfers were matched to their bills", settled)
    moved = await service.evaluate_pending()
    if moved:
        logger.info("%d decisions were made or moved", moved)


@jobs.task("decisions.evaluate_one", retry=1)
async def evaluate_one(company_id: str) -> None:
    """One company's bills, weighed now rather than on the next pass."""
    await service.evaluate_company(uuid.UUID(company_id))


# Two minutes after each evaluation pass, so what it moved is weighed in the same five minutes.
@jobs.periodic("decisions.advise", cron="2-59/5 * * * *")
async def advise(timestamp: int) -> None:
    """The controller's opinion on each evaluation that waits for the owner, once."""
    advised = await advising.advise_pending()
    if advised:
        logger.info("the controller weighed %d bills", advised)


@jobs.periodic("decisions.anchor", cron="* * * * *")
async def anchor(timestamp: int) -> None:
    """The companies' log heads on the chain, when any moved: within the minute after a payment,
    otherwise every ANCHOR_EVERY."""
    if await anchoring.anchor_heads():
        logger.info("the decision log's heads were anchored")
