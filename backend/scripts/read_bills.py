"""Has the bill reader read the bills whose file is kept and that were not read for their lines,
what the company owes first. The periodic job does the same a few bills a minute; this does it
now, after keep_bill_files.py has fetched the files. The plates it reads place a bill on its
trucks' jobs at the logistics pack's next filing, within ten minutes.

uv run python scripts/read_bills.py                  (the pilot company, a dry run)
uv run python scripts/read_bills.py --write
uv run python scripts/read_bills.py --company-id <id> --write --limit 50

A dry run prints the counts and asks no model. A write asks the model once a bill, within the
day's budget (BILLS_READ_PER_DAY; with 0 it reads nothing), and writes only Tamga's own
database. Safe to run again: a bill read once is not read again.
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import packs  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.modules.books import service as books  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db  # noqa: E402


async def read(
    company_id: uuid.UUID, *, write: bool, limit: int
) -> tuple[books.BillsWaiting, books.BillsRead | None]:
    packs.register_all()
    # A fact names the item it was read in: every table must be known before one is written.
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        waiting = await books.bills_waiting(company_id, at_most=5000)
        return waiting, await books.read_bills(company_id, limit=limit) if write else None
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="read the bills; else a dry run")
    parser.add_argument("--limit", type=int, default=300, help="at most this many bills")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    waiting, done = asyncio.run(read(args.company_id, write=args.write, limit=args.limit))
    print(f"bills waiting to be read: {waiting.to_read}")
    print(f"  of which the file itself is not read yet: {waiting.file_waiting}")
    print(f"reads left in the day's budget: {waiting.left}")
    if done is None:
        print(f"dry run: --write reads up to {min(waiting.to_read, waiting.left, args.limit)}")
        return 0
    print(f"read: {done.read}; lines left out, their words not in the file: {done.dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
