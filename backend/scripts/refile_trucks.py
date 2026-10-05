"""Proposes every project's trucks again under the rules of today (ADR-0021): the trucks code
proposed and the owner has had no hand in are dropped, then the project's own papers propose
them again and its items are placed on them. A truck the owner confirmed, rejected, wrote on,
marked, followed or placed a paper on stays as it is. Bills stay where they are.

Read the plates' roles first (scripts/reread_facts.py, scripts/remap_books.py), so a trailer is
known as one.

uv run python scripts/refile_trucks.py                  (the pilot company, a dry run)
uv run python scripts/refile_trucks.py --write
uv run python scripts/refile_trucks.py --company-id <id> --write

A dry run prints each project's trucks before and after and writes nothing. Only Tamga's own
database is written; no model is asked. Safe to run again.
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
from app.packs.logistics import filing  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db, open_session  # noqa: E402


async def refile(company_id: uuid.UUID, *, write: bool) -> list[filing.Refiled]:
    packs.register_all()
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            refiled = await filing.refile_company(db, company_id)
            await (db.commit() if write else db.rollback())
            return refiled
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="write the trucks; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    refiled = asyncio.run(refile(args.company_id, write=args.write))
    print(f"{'project':<12} {'expected':>8} {'before':>6} {'after':>5}  dropped, proposed")
    for each in sorted(refiled, key=lambda each: each.number):
        if each.before or each.after:
            expected = "?" if each.expected is None else str(each.expected)
            print(
                f"{each.number:<12} {expected:>8} {each.before:>6} {each.after:>5}"
                f"  -{each.dropped} +{each.proposed}"
            )
    print(f"trucks: {sum(e.before for e in refiled)} -> {sum(e.after for e in refiled)}")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
