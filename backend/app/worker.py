"""The worker process: runs deferred and periodic jobs.

python -m app.worker        (in production, the backend image with this command)

Same image and settings as the api; a job gets its database session from
``app.platform.db.open_session``.
"""

import asyncio
from typing import Final

from app import packs
from app.config import get_settings
from app.platform import isolated, jobs, logs, tracing
from app.platform.db import dispose_db, init_db

# Jobs run side by side. A scheduled job never overlaps itself (its lock), so these are different
# jobs: sorting a backlog of mail does not make reading files or polling a mailbox wait.
CONCURRENCY: Final = 4


async def main() -> None:
    settings = get_settings()
    logs.configure(settings.log_level)
    packs.register_all()
    jobs.import_all_tasks()
    init_db(str(settings.database_url))
    # Before any other thread starts: the forkserver starts with a narrowed environment.
    isolated.start()
    tracing.init_tracing("worker")
    try:
        async with jobs.app.open_async():
            await jobs.app.run_worker_async(concurrency=CONCURRENCY)
    finally:
        # Sending what is batched waits on the network: never on the event loop.
        await asyncio.to_thread(tracing.shutdown_tracing)
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
