"""Suggests each bill no project holds yet to the one project that holds an item sharing a key
number with it, as the place job does after each run (linking.place_invoices). Run the books
remap first (scripts/remap_books.py), so the bills stored before carry their facts.

uv run python scripts/place_invoices.py                  (the pilot company, a dry run)
uv run python scripts/place_invoices.py --write
uv run python scripts/place_invoices.py --company-id <id> --write

A dry run prints the counts and writes nothing. Only Tamga's own database is written; no model is
asked and the accounting system is not read. Safe to run again.
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
from app.modules.linking import service  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db, open_session  # noqa: E402


async def place(company_id: uuid.UUID, *, write: bool) -> service.BillsPlaced:
    packs.register_all()
    # A link names the user who decided it: every table must be known before one is written.
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            placed = await service.place_invoices(db, company_id)
            await (db.commit() if write else db.rollback())
            return placed
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="write the links; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    placed = asyncio.run(place(args.company_id, write=args.write))
    print(f"  suggested: {placed.linked} bill(s)")
    print(f"    several: {placed.several} bill(s) whose numbers point to more than one project")
    print(f"       none: {placed.none} bill(s) no project shares a number with")
    print(f"    another: {placed.contradicted} sale(s) whose project has another client")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
