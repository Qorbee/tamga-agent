"""Works out the firms on each project from what names them (docs/architecture/09): two kinds of
signal that agree make a firm the project's until the owner answers; one is asked of the owner.
The worker does this after each run that places conversations; this runs it once, for a company
whose worker is stopped.

uv run python scripts/suggest_firms.py                  (the pilot company, a dry run)
uv run python scripts/suggest_firms.py --write
uv run python scripts/suggest_firms.py --company-id <id> --write

A dry run prints, per project and role, how many firms two signals agree on and how many would be
asked of the owner, and writes nothing. No model is asked.
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
from app.modules.linking import firms  # noqa: E402
from app.platform.db import dispose_db, import_all_models, init_db, open_session  # noqa: E402


async def suggest(company_id: uuid.UUID, *, write: bool) -> firms.Found:
    packs.register_all()
    import_all_models()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            found = await firms.suggest_firms(db, company_id)
            await (db.commit() if write else db.rollback())
            return found
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument(
        "--write", action="store_true", help="write the suggestions; else a dry run"
    )
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("set PILOT_COMPANY_ID or pass --company-id")
    found = asyncio.run(suggest(args.company_id, write=args.write))
    for number, lines in sorted(found.by_project.items()):
        print(f"{number:12} {'; '.join(lines)}")
    print(f"firms: {found.agreed} agreed, {found.suggested - found.agreed} to ask the owner")
    print("written" if args.write else "dry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
