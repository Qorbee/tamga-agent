"""Asks the books ahead for the files of the bills that hold none yet but name an e-document,
the newest first, so each opens at once and is read for its plates and numbers; and asks for
each invoice file kept before and never read to be read.

uv run python scripts/keep_bill_files.py                  (the pilot company, a dry run)
uv run python scripts/keep_bill_files.py --write
uv run python scripts/keep_bill_files.py --company-id <id> --write --limit 50

A dry run prints the counts and reads nothing from the books. A write asks the books once a
bill, from the month's budget of reads (BOOKS_MONTHLY_READS), and only reads: nothing is
written there. Safe to run again: a bill that holds its file is not asked for again.
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
from app.modules.books import service  # noqa: E402
from app.platform.db import dispose_db, init_db  # noqa: E402


async def keep(company_id: uuid.UUID, *, write: bool, limit: int) -> service.FilesKept:
    packs.register_all()
    init_db(str(get_settings().database_url))
    try:
        return await service.keep_bill_files(company_id, write=write, limit=limit)
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="ask the books and keep the files")
    parser.add_argument("--limit", type=int, default=300, help="at most this many bills")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("no --company-id, and no PILOT_COMPANY_ID in settings")
    kept = asyncio.run(keep(args.company_id, write=args.write, limit=args.limit))
    print(f"bills without a file that name an e-document: {kept.without}")
    print(f"invoice files kept before and never read: {kept.to_read}")
    if args.write:
        print(f"files kept: {kept.kept}; the books had none: {kept.none}")
    else:
        print(f"dry run: --write asks the books for up to {min(kept.without, args.limit)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
