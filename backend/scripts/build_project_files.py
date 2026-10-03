"""Brings the files each linked mail carried into its projects, for links made before files
followed their mail (ADR-0021): a file is suggested, confirmed or rejected in a project as its
mail is there. Links the owner made or answered are left as they are.

uv run python scripts/build_project_files.py                  (the pilot company, a dry run)
uv run python scripts/build_project_files.py --write
uv run python scripts/build_project_files.py --company-id <id> --write

A dry run prints what would be written and writes nothing. Only Tamga's own database is written;
no model is asked and nothing reaches a mailbox. Safe to run again.
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
from app.modules.projects import service  # noqa: E402
from app.modules.projects.models import LinkStatus  # noqa: E402
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402


async def build(company_id: uuid.UUID, *, write: bool) -> dict[LinkStatus, int]:
    packs.register_all()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            counts = await service.carry_all_files(db, company_id)
            await (db.commit() if write else db.rollback())
            return counts
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--write", action="store_true", help="write the links; else a dry run")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    counts = asyncio.run(build(args.company_id, write=args.write))
    for status in LinkStatus:
        print(f"{status.value:>10}: {counts.get(status, 0)} file link(s)")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
