"""Count what Tamga did for real companies, for the traction updates and the README: counts only,
never a name, an amount or a document, so the output can be posted as it is.

python scripts/traction.py                  every pilot company, all time
python scripts/traction.py --since 2026-10-01
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402

# Each line: what it counts, and the query over the pilot companies' rows since the day given.
# Every table here carries company_id (ADR-0003); the filter is the pilot companies'.
COUNTS: dict[str, str] = {
    "messages received (mail, uploads, links)": "SELECT count(*) FROM messages "
    "WHERE direction = 'inbound' "
    "AND company_id = ANY(:companies) AND received_at >= :since",
    "files read": "SELECT count(*) FROM documents WHERE company_id = ANY(:companies) "
    "AND created_at >= :since",
    "invoices in the books (all time)": "SELECT count(*) FROM invoices "
    "WHERE company_id = ANY(:companies)",
    "projects": "SELECT count(*) FROM projects WHERE company_id = ANY(:companies) "
    "AND created_at >= :since",
    "items placed on projects": "SELECT count(*) FROM placements "
    "WHERE company_id = ANY(:companies) AND created_at >= :since",
    "bills evaluated": "SELECT count(*) FROM decisions WHERE company_id = ANY(:companies) "
    "AND created_at >= :since",
    "owner's answers on bills": "SELECT count(*) FROM decision_log "
    "WHERE company_id = ANY(:companies) AND user_id IS NOT NULL AND at >= :since",
    "test-USDC payments settled": "SELECT count(*) FROM payments "
    "WHERE company_id = ANY(:companies) AND state = 'settled' AND kind = 'payment' "
    "AND usdc_units IS NOT NULL AND created_at >= :since",
    "bank payments mirrored in test USDC on Arc": "SELECT count(*) FROM payments "
    "WHERE company_id = ANY(:companies) AND state = 'settled' AND kind = 'mirror' "
    "AND created_at >= :since",
    # One anchor holds every company's log head, sandboxes' too: it is not a pilot count.
    "log anchors on Arc (one root over every company, sandboxes included)": "SELECT count(*) "
    "FROM decision_anchors "
    "WHERE state = 'settled' AND created_at >= :since",
    "chat runs the owner began": "SELECT count(*) FROM chat_runs "
    "WHERE company_id = ANY(:companies) AND began_by = 'owner' AND ran_at >= :since",
    "chats Tamga began": "SELECT count(*) FROM chats WHERE company_id = ANY(:companies) "
    "AND origin = 'tamga' AND created_at >= :since",
    "memory entries the owner confirmed": "SELECT count(*) FROM memory_entries "
    "WHERE company_id = ANY(:companies) AND state = 'held' AND created_at >= :since",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=date.fromisoformat, default=date(2000, 1, 1))
    args = parser.parse_args()
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            companies = list(
                (await db.execute(text("SELECT id FROM companies WHERE kind = 'pilot'"))).scalars()
            )
            print(f"pilot companies: {len(companies)}, since {args.since}")
            for label, query in COUNTS.items():
                try:
                    count = (
                        await db.execute(text(query), {"companies": companies, "since": args.since})
                    ).scalar_one()
                except Exception as error:  # one stale query never hides the other counts
                    await db.rollback()
                    print(f"{label}: unavailable ({type(error).__name__})")
                    continue
                print(f"{label}: {count}")
    finally:
        await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
