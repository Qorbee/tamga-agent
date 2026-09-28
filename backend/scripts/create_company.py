"""Create a company and print its first owner's invite link.

uv run python scripts/create_company.py --name "Acme Lojistik" --owner-email owner@example.com

Nobody is signed in yet to invite the first owner, so this is the one way in. The owner opens the
link and sets their own password; no password passes through this command. A pilot company starts in
shadow mode (ADR-0009).
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Running a file puts its own directory on sys.path, not backend/, so `import app` would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.modules.companies.models import CompanyKind  # noqa: E402
from app.modules.identity import service  # noqa: E402
from app.platform.db import dispose_db, get_session, init_db  # noqa: E402


async def create(name: str, kind: CompanyKind, owner_email: str) -> str:
    init_db(str(get_settings().database_url))
    try:
        async for db in get_session():
            return await service.bootstrap_company(
                db, name=name, kind=kind, owner_email=owner_email
            )
        raise RuntimeError("no database session")
    finally:
        await dispose_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument(
        "--kind",
        choices=[CompanyKind.PILOT.value, CompanyKind.DEMO.value],
        default=CompanyKind.PILOT.value,
        help="sandboxes are created by guest sign-in, not here",
    )
    args = parser.parse_args()

    url = asyncio.run(create(args.name, CompanyKind(args.kind), args.owner_email))
    print(f"Owner invite (shown once, valid {get_settings().invite_days} days):\n{url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
