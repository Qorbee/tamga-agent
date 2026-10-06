"""Places again, under the rules of today, the mails each conversation brought after its first
into the one project code tied it to (docs/architecture/09): its first mail stays, the others are
placed again one by one in the order they were sent, code following where nothing is new and the
linker reading where a mail names what its job does not hold. A conversation the owner tied or
answered anywhere stays as it is.

uv run python scripts/recut_threads.py                  (the pilot company, a dry run)
uv run python scripts/recut_threads.py --write --max-reads 100
uv run python scripts/recut_threads.py --company-id <id> --write --max-reads 100

A dry run counts the conversations and mails it would place again, and about how many model
reads that takes; it writes nothing and asks no model. With --write the linker is asked outside
the daily budget, starting no model run once --max-reads requests were made (one under way can
go past it by its own few): what is left then, or what failed, keeps its conversation waiting
for the worker. Stop the worker while it runs. It works out the projects' names and days again
at the end. Then file the trucks again (scripts/refile_trucks.py) and recheck the bills
(scripts/recheck_invoice_links.py).
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
from app.platform.db import dispose_db, import_all_models, init_db  # noqa: E402


async def recut(company_id: uuid.UUID, *, write: bool, max_reads: int) -> service.Recut:
    packs.register_all()
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        return await service.recut_threads(company_id, write=write, max_reads=max_reads)
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="place them again; else a dry run")
    parser.add_argument(
        "--max-reads", type=int, default=0, help="model requests after which no run starts"
    )
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    if args.write and args.max_reads <= 0:
        parser.error("--write needs --max-reads: run it dry first and give the count room")
    done = asyncio.run(recut(args.company_id, write=args.write, max_reads=args.max_reads))
    print(f"conversations: {done.threads}")
    print(f"        mails: {done.mails} to place again")
    print(f"  model reads: about {done.news}")
    if args.write:
        print(f"       placed: {done.placed}")
        print(f"   reads made: {done.reads} of {args.max_reads}")
        print(f"       failed: {done.failed}")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
