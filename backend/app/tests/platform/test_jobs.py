from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform import jobs

pytestmark = pytest.mark.integration

ran: list[dict[str, Any]] = []


@jobs.task("tests.record")
async def record(**kwargs: Any) -> None:
    ran.append(kwargs)


async def queued(db: AsyncSession) -> list[Any]:
    rows = await db.execute(
        text(
            "SELECT task_name, args, lock FROM procrastinate_jobs WHERE task_name = 'tests.record'"
        )
    )
    return list(rows)


async def test_a_job_exists_only_if_its_transaction_commits(db: AsyncSession) -> None:
    await jobs.defer(db, record, args={"n": 1})
    await db.rollback()
    assert await queued(db) == []

    await jobs.defer(db, record, args={"n": 2}, lock="payee:42")
    await db.commit()
    assert await queued(db) == [("tests.record", {"n": 2}, "payee:42")]


async def test_the_worker_runs_a_deferred_job(db: AsyncSession) -> None:
    ran.clear()
    await jobs.defer(db, record, args={"invoice": "ABC-1"})
    await db.commit()

    async with jobs.app.open_async():
        # wait=False: work until the queue is empty, then return.
        await jobs.app.run_worker_async(wait=False, install_signal_handlers=False)

    assert ran == [{"invoice": "ABC-1"}]
