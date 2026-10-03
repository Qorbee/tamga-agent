"""Types by code the company's files that were read before the rules existed: a CMR by its
printed boxes or its name, a Turkish e-document by its profile. Only a file with no type is
typed; one the owner typed is never touched.

uv run python scripts/classify_documents.py                  (the pilot company, a dry run)
uv run python scripts/classify_documents.py --write
uv run python scripts/classify_documents.py --company-id <id> --write

A dry run prints what would be typed and writes nothing. Only Tamga's own database is written;
no model is asked. Safe to run again.
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
from app.modules.documents import service  # noqa: E402
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402


async def classify(company_id: uuid.UUID, *, write: bool) -> dict[str, int]:
    packs.register_all()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            counts = await service.classify_read(db, company_id)
            await (db.commit() if write else db.rollback())
            return counts
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="write the types; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    counts = asyncio.run(classify(args.company_id, write=args.write))
    for key, count in sorted(counts.items()):
        print(f"{key:>16}: {count} file(s)")
    print(f"{sum(counts.values())} typed" + ("" if args.write else ", dry run, nothing written"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
