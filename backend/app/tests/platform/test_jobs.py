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


async def status_of(db: AsyncSession, job_id: int) -> str:
    return str(
        await db.scalar(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"), {"id": job_id}
        )
    )


async def left_doing(db: AsyncSession, job_id: int, *, worker_id: int | None = None) -> None:
    """The job as a worker leaves it when it dies mid-run."""
    await db.execute(
        text("UPDATE procrastinate_jobs SET status = 'doing', worker_id = :worker WHERE id = :id"),
        {"id": job_id, "worker": worker_id},
    )
    await db.commit()


async def test_a_job_a_dead_worker_left_running_is_queued_again(db: AsyncSession) -> None:
    ran.clear()
    job_id = await jobs.defer(db, record, args={"n": 1}, lock="payee:42")
    await left_doing(db, job_id)

    async with jobs.app.open_async():
        assert await jobs.release_stalled() == 1
        assert await status_of(db, job_id) == "todo"
        await jobs.app.run_worker_async(wait=False, install_signal_handlers=False)

    assert ran == [{"n": 1}]


async def scheduled(db: AsyncSession) -> int:
    """A run of a scheduled job, which carries its name as lock and queueing lock."""
    job_id = await jobs.defer(db, record, lock="tests.scheduled")
    await db.execute(
        text("UPDATE procrastinate_jobs SET queueing_lock = 'tests.scheduled' WHERE id = :id"),
        {"id": job_id},
    )
    await db.commit()
    return job_id


async def test_a_scheduled_job_left_running_is_queued_again(db: AsyncSession) -> None:
    left = await scheduled(db)
    await left_doing(db, left)

    async with jobs.app.open_async():
        assert await jobs.release_stalled() == 1

    assert await status_of(db, left) == "todo"


async def test_a_scheduled_job_left_running_is_closed_when_its_next_run_already_waits(
    db: AsyncSession,
) -> None:
    left = await scheduled(db)
    await left_doing(db, left)
    following = await scheduled(db)

    async with jobs.app.open_async():
        assert await jobs.release_stalled() == 1

    assert await status_of(db, left) == "failed"
    assert await status_of(db, following) == "todo"


async def test_a_job_a_living_worker_runs_is_left_alone(db: AsyncSession) -> None:
    job_id = await jobs.defer(db, record)
    worker_id = await db.scalar(text("SELECT worker_id FROM procrastinate_register_worker_v1()"))
    await left_doing(db, job_id, worker_id=worker_id)

    async with jobs.app.open_async():
        assert await jobs.release_stalled() == 0

    assert await status_of(db, job_id) == "doing"
