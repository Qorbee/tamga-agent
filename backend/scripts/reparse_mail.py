"""Reads stored mail again, to fill what the parser learned to keep after it was stored: the
sender's name, which files a mail's HTML body shows inline, and whether it is bulk mail.

uv run python scripts/reparse_mail.py                  (the pilot company, PILOT_COMPANY_ID)
uv run python scripts/reparse_mail.py --company-id <id>

Reads storage only; nothing reaches a mailbox. Safe to run again.
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.modules.conversations import service  # noqa: E402
from app.platform.db import dispose_db, get_session, init_db  # noqa: E402


async def reparse(company_id: uuid.UUID) -> int:
    init_db(str(get_settings().database_url))
    read = 0
    try:
        async for db in get_session():
            after: uuid.UUID | None = None
            while True:
                after = await service.reread_stored_mail(db, company_id, after=after)
                await db.commit()
                if after is None:
                    return read
                read += 1
        raise RuntimeError("no database session")
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    batches = asyncio.run(reparse(args.company_id))
    print(f"Read {batches} batch(es) of stored mail again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
