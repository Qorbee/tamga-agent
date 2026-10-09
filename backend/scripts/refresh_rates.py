"""Read the official reference rates now (ADR-0028), rather than at the job's next hour:
`deploy/tamga` runs it once the database is migrated, so a server just deployed does not hold
every bill in another currency than its policy's until the first run.

python scripts/refresh_rates.py
"""

import asyncio
import sys
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import packs  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.modules.fx import service as fx  # noqa: E402
from app.platform.clock import utcnow  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db  # noqa: E402


async def main() -> None:
    packs.register_all()
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        print(f"read {await fx.refresh(utcnow().date())} reference rates")
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
