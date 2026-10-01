"""Background jobs over procrastinate, on the same PostgreSQL (ADR-0001).

Modules use two things from here and never import procrastinate: ``task`` to declare a job and
``defer`` to enqueue one. ``defer`` writes the job in the caller's own transaction by calling
procrastinate's SQL function, so a job exists only if the work that asked for it commits: a payment
job and its decision commit together or not at all. The worker (``python -m app.worker``) runs the
jobs over psycopg, procrastinate's own driver.

A job's lock (``payee:<id>``, ``thread:<id>``) keeps two jobs that share it from running at once.
A worker that dies mid-job leaves the job ``doing`` and its lock held; ``release_stalled`` queues it
again within a minute, so every job must be safe to run twice.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Final

import procrastinate
from procrastinate.exceptions import UniqueViolation
from procrastinate.jobs import Status
from procrastinate.tasks import Task
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.platform.db import import_from_each_module

DEFAULT_QUEUE: Final = "default"
DEFAULT_PRIORITY: Final = 0
RELEASE_STALLED: Final = "jobs.release_stalled"

type Job = Task[..., Any, ...]

# The connector connects only when the worker (or a test) opens the app, never at import.
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=get_settings().database_url_libpq)
)

# procrastinate_defer_jobs_v1 is the function procrastinate's own defer calls. Its schema is the
# vendored revision in app/migrations, so a procrastinate upgrade that changes it arrives as a new
# revision, and this statement changes with it.
_DEFER: Final = text(
    "SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[ROW("
    "CAST(:queue AS varchar), CAST(:task AS varchar), CAST(:priority AS integer),"
    " CAST(:lock AS text), NULL, CAST(:args AS jsonb), CAST(:run_at AS timestamptz)"
    ")::procrastinate_job_to_defer_v1]))"
)


def task(
    name: str, *, queue: str = DEFAULT_QUEUE, retry: int = 0
) -> Callable[[Callable[..., Awaitable[None]]], Job]:
    """Declares a job. ``name`` is stored with every queued job, so it outlives renames of the
    function: change it only with the queue empty."""

    def register(function: Callable[..., Awaitable[None]]) -> Job:
        job: Job = app.task(name=name, queue=queue, retry=retry)(function)
        return job

    return register


def periodic(name: str, *, cron: str) -> Callable[[Callable[[int], Awaitable[None]]], Job]:
    """Declares a job the worker defers on a schedule (``cron``, e.g. ``"*/2 * * * *"``). It gets
    the scheduled time as a Unix timestamp. Runs of one job never overlap, and a run that is still
    queued when the next is due absorbs it, so a slow run never builds a backlog."""

    def register(function: Callable[[int], Awaitable[None]]) -> Job:
        job: Job = app.task(name=name, lock=name, queueing_lock=name)(function)
        app.periodic(cron=cron)(job)
        return job

    return register


async def defer(
    db: AsyncSession,
    job: Job,
    *,
    args: dict[str, Any] | None = None,
    run_at: datetime | None = None,
    lock: str | None = None,
) -> int:
    """Enqueues ``job`` in the caller's transaction and returns its id. ``args`` must be JSON:
    ids and plain values, never an ORM object.

    ``run_at`` is wall-clock time, when the worker may start it. A business deadline ("remind in
    two days" in a sandbox's or a replay's time) is not a delayed job: it is a timer row that
    ``clock.now(company)`` decides is due (docs/architecture/02, Timers are rows)."""
    job_id = await db.scalar(
        _DEFER,
        {
            "queue": job.queue,
            "task": job.name,
            "priority": DEFAULT_PRIORITY,
            "lock": lock,
            "args": json.dumps(args or {}),
            "run_at": run_at,
        },
    )
    if job_id is None:
        raise RuntimeError(f"procrastinate returned no id for {job.name}")
    return int(job_id)


async def release_stalled() -> int:
    """Frees the jobs a dead worker left ``doing``, and returns how many. A job counts as left
    when its worker has sent no heartbeat for half a minute or is gone.

    Each is queued again. The one that cannot be is a scheduled job whose next run already waits
    (two queued runs of one job collide): that next run does its work, so it is closed as failed."""
    stalled = list(await app.job_manager.get_stalled_jobs())
    for job in stalled:
        try:
            await app.job_manager.retry_job(job)
        except UniqueViolation:
            await app.job_manager.finish_job(job, Status.FAILED, delete_job=False)
    return len(stalled)


# Without a lock, unlike every other scheduled job: a run of this one that a dead worker left behind
# must not keep the next from starting, or nothing would ever free it.
@app.periodic(cron="* * * * *")
@app.task(name=RELEASE_STALLED, queueing_lock=RELEASE_STALLED)
async def _release_stalled(timestamp: int) -> None:
    await release_stalled()


def import_all_tasks() -> None:
    """Import every module's ``tasks.py``, so the worker knows every job it may be handed."""
    import_from_each_module("tasks")
