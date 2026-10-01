"""The worker process: runs deferred and periodic jobs.

python -m app.worker        (in production, the backend image with this command)

Same image and settings as the api; a job gets its database session from
``app.platform.db.open_session``.
"""

import asyncio
import logging

from app import packs
from app.config import get_settings
from app.platform import isolated, jobs, tracing
from app.platform.db import dispose_db, init_db


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    packs.register_all()
    jobs.import_all_tasks()
    init_db(str(settings.database_url))
    # Before any other thread starts: the forkserver starts with a narrowed environment.
    isolated.start()
    tracing.init_tracing("worker")
    try:
        async with jobs.app.open_async():
            await jobs.app.run_worker_async()
    finally:
        # Sending what is batched waits on the network: never on the event loop.
        await asyncio.to_thread(tracing.shutdown_tracing)
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
