"""Works out again the days Tamga gave each project from what its conversations say, and queues
each project only Tamga named to be named again, after the way they are worked out changed.
The owner's own names and days are left as they are.

uv run python scripts/tell_projects_again.py                  (the pilot company, a dry run)
uv run python scripts/tell_projects_again.py --write
uv run python scripts/tell_projects_again.py --company-id <id> --write

A dry run prints which projects would be named and writes nothing. Only Tamga's own database is
written; the worker then names each project: the titler writes a name, which code keeps only
when every word of it is in what the titler was shown, else code's name from the client, route
and first day. One request of a small model a project, from the day's budget of model reads.
Nothing reaches a mailbox or the books. Safe to run again.
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
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402


async def tell(company_id: uuid.UUID, *, write: bool) -> list[str]:
    packs.register_all()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            named = await service.tell_projects_again(db, company_id)
            await (db.commit() if write else db.rollback())
            return named
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="write the changes; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    named = asyncio.run(tell(args.company_id, write=args.write))
    print(", ".join(named))
    print(
        f"{len(named)} project(s) to be named by the titler"
        + ("" if args.write else " (dry run, nothing written)")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
