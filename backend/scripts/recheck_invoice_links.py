"""Removes the bills code placed in a project that the rules of today would not place there: one
placed by its plate where the project's firm in the bill's role is not the bill's or is not
known, and a sale placed by a shared number where the project's client is another firm. A link
the owner made or answered stays.

uv run python scripts/recheck_invoice_links.py                  (the pilot company, a dry run)
uv run python scripts/recheck_invoice_links.py --write
uv run python scripts/recheck_invoice_links.py --company-id <id> --write

A dry run prints how many links would go and writes nothing. Only Tamga's own database is
written; no model is asked and the accounting system is not read. Safe to run again.
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
from app.modules.linking import service as linking  # noqa: E402
from app.packs.logistics import filing  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db, open_session  # noqa: E402


async def recheck(company_id: uuid.UUID, *, write: bool) -> tuple[int, int]:
    packs.register_all()
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            by_plate = await filing.recheck_bills_by_plate(db, company_id)
            by_number = await linking.recheck_invoices(db, company_id)
            await (db.commit() if write else db.rollback())
            return by_plate, by_number
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="remove the links; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    by_plate, by_number = asyncio.run(recheck(args.company_id, write=args.write))
    print(f" by plate: {by_plate} link(s) removed")
    print(f"by number: {by_number} link(s) removed")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
