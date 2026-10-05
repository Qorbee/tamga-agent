"""Reads facts of the kinds named again from the company's read mail and files, for a kind code
learned to find after they were read (a plate's role: truck_plate, trailer_plate). The facts they
have stay; an invoice's are read again by scripts/remap_books.py.

uv run python scripts/reread_facts.py --kind truck_plate --kind trailer_plate            (dry run)
uv run python scripts/reread_facts.py --kind truck_plate --kind trailer_plate --write
uv run python scripts/reread_facts.py --company-id <id> --kind <kind> --write

A dry run prints how many facts it found and writes nothing. Only Tamga's own database is written;
no model is asked and nothing reaches a mailbox. Safe to run again.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app import packs  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.modules.conversations import service as conversations  # noqa: E402
from app.modules.documents import service as documents  # noqa: E402
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402

type Reader = Callable[..., Awaitable[tuple[int, uuid.UUID | None]]]


async def reread(company_id: uuid.UUID, kinds: frozenset[str], *, write: bool) -> dict[str, int]:
    packs.register_all()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            found = {
                "mail": await _all(db, conversations.reread_facts, company_id, kinds),
                "files": await _all(db, documents.reread_facts, company_id, kinds),
            }
            await (db.commit() if write else db.rollback())
            return found
    finally:
        await dispose_db()


async def _all(
    db: AsyncSession, reader: Reader, company_id: uuid.UUID, kinds: frozenset[str]
) -> int:
    found, after = await reader(db, company_id, kinds, after=None)
    while after is not None:
        more, after = await reader(db, company_id, kinds, after=after)
        found += more
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--kind", action="append", required=True, help="a fact kind to read")
    parser.add_argument("--write", action="store_true", help="write the facts; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    found = asyncio.run(reread(args.company_id, frozenset(args.kind), write=args.write))
    for source, count in found.items():
        print(f"{source:>6}: {count} fact(s)")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
