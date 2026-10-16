"""How Tamga's readings hold up against the owner's answers (ADR-0023): of what Tamga put on a
project (a mail, a file, a bill, a firm, a truck, a new project), how much the owner kept, how
much they refused, and how much still waits; and what came in another way ("added": the owner's
own, or older than what proposes it now).

uv run python scripts/score.py                     (the pilot company)
uv run python scripts/score.py --company-id <id>
uv run python scripts/score.py --json > before.json
uv run python scripts/score.py --compare before.json

Reads only, counts only: no name, no amount, no text. A refused item is a miss of Tamga's; a
kept one is agreement. Who proposed an item is read from what it keeps: a link answered after it
was made, a project a placement proposed, a firm with signals, a truck with evidence.

It scores what is stored, as the owner answered it: how Tamga has done so far, and how a change
does from its deploy on. It does not run a changed reader over the answered cases before a
merge: that replay is ADR-0023's next step.
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.platform.db import dispose_db, init_db, open_session  # noqa: E402

# A link the owner made themselves is answered as it is made; one Tamga suggested is answered
# later (`answered`, more than five seconds after). The gap tells them apart, since an answer
# keeps no trace of who placed it.
# Each across the module tables it scores, read only; every one by the company.
QUERIES: dict[str, str] = {
    "links": """
        SELECT item_kind AS kind,
               count(*) FILTER (WHERE status = 'confirmed' AND answered) AS kept,
               count(*) FILTER (WHERE status = 'confirmed' AND decided_by IS NULL) AS kept_by_code,
               count(*) FILTER (WHERE status = 'rejected' AND answered) AS refused,
               count(*) FILTER (WHERE status = 'suggested') AS waiting,
               count(*) FILTER (WHERE status = 'confirmed' AND decided_by IS NOT NULL
                                AND NOT answered) AS added_by_owner
        FROM (SELECT *, decided_at > created_at + interval '5 seconds' AS answered
              FROM project_links WHERE company_id = :company) AS links
        GROUP BY item_kind ORDER BY item_kind""",
    "projects": """
        SELECT 'proposed project' AS kind,
               count(*) FILTER (WHERE proposed AND p.status IN ('active', 'closed')) AS kept,
               0 AS kept_by_code,
               count(*) FILTER (WHERE proposed AND p.status = 'rejected') AS refused,
               count(*) FILTER (WHERE proposed AND p.status = 'proposed') AS waiting,
               count(*) FILTER (WHERE NOT proposed AND p.status <> 'rejected') AS added_by_owner
        FROM (SELECT *, EXISTS (
                SELECT 1 FROM placements pl
                WHERE pl.company_id = :company AND pl.project_id = projects.id
                  AND pl.outcome = 'proposed') AS proposed
              FROM projects WHERE company_id = :company) AS p""",
    "firms": """
        SELECT 'firm on a project' AS kind,
               count(*) FILTER (WHERE state = 'confirmed' AND cardinality(signals) > 0) AS kept,
               0 AS kept_by_code,
               count(*) FILTER (WHERE state = 'rejected') AS refused,
               count(*) FILTER (WHERE state = 'suggested') AS waiting,
               count(*) FILTER (WHERE state = 'confirmed' AND cardinality(signals) = 0)
                 AS added_by_owner
        FROM project_parties WHERE company_id = :company""",
    "trucks": """
        SELECT 'truck' AS kind,
               count(*) FILTER (WHERE read AND state = 'confirmed') AS kept,
               0 AS kept_by_code,
               count(*) FILTER (WHERE read AND state = 'rejected') AS refused,
               count(*) FILTER (WHERE read AND state = 'proposed') AS waiting,
               count(*) FILTER (WHERE NOT read AND state = 'confirmed') AS added_by_owner
        FROM (SELECT *, cardinality(evidence) > 0 AS read
              FROM shipments WHERE company_id = :company) AS trucks""",
}
COLUMNS = ("kept", "kept_by_code", "refused", "waiting", "added_by_owner")


def agreement(row: dict[str, Any]) -> float | None:
    """Of what the owner answered, how much they kept."""
    answered = row["kept"] + row["refused"]
    return row["kept"] / answered if answered else None


async def score(company_id: uuid.UUID) -> list[dict[str, Any]]:
    init_db(str(get_settings().database_url))
    try:
        async with open_session() as db:
            rows: list[dict[str, Any]] = []
            for query in QUERIES.values():
                result = await db.execute(text(query), {"company": company_id})
                rows.extend(dict(row) for row in result.mappings())
            await db.rollback()
            return rows
    finally:
        await dispose_db()


def show(rows: list[dict[str, Any]], before: dict[str, dict[str, Any]] | None) -> None:
    print(f"{'':20}{'kept':>8}{'by code':>9}{'refused':>9}{'waiting':>9}{'added':>8}{'agree':>8}")
    for row in rows:
        rate = agreement(row)
        line = f"{row['kind']:20}" + "".join(
            f"{row[column]:>{8 if column in ('kept', 'added_by_owner') else 9}}"
            for column in COLUMNS
        )
        line += f"{f'{rate:.0%}' if rate is not None else '—':>8}"
        was = (before or {}).get(row["kind"])
        if was is not None:
            then = agreement(was)
            if then is not None and rate is not None:
                line += f"   was {then:.0%} ({rate - then:+.0%})"
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", type=uuid.UUID, default=get_settings().pilot_company_id)
    parser.add_argument("--json", action="store_true", help="print the counts as JSON")
    parser.add_argument("--compare", type=Path, help="a --json run to compare against")
    args = parser.parse_args()
    if args.company_id is None:
        parser.error("no --company-id, and no PILOT_COMPANY_ID in settings")
    rows = asyncio.run(score(args.company_id))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    before = None
    if args.compare:
        before = {row["kind"]: row for row in json.loads(args.compare.read_text())}
    show(rows, before)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
