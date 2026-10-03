"""Maps the company's stored accounting records again from what their source gave, so a field
the mapping has learned since (an invoice's description) fills without reading the source.

uv run python scripts/remap_books.py                  (the pilot company, a dry run)
uv run python scripts/remap_books.py --write
uv run python scripts/remap_books.py --company-id <id> --write

A dry run prints how many invoices would change and writes nothing. Only Tamga's own database is
written; the accounting system is not read. Safe to run again.
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
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402


async def remap(company_id: uuid.UUID, *, write: bool) -> int:
    packs.register_all()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            changed = await service.remap_invoices(db, company_id)
            await (db.commit() if write else db.rollback())
            return changed
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="write the changes; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    changed = asyncio.run(remap(args.company_id, write=args.write))
    print(f"{changed} invoice(s) {'changed' if args.write else 'would change'}")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
