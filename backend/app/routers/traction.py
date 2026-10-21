"""What Tamga has done for the real companies using it, in counts: the landing page's figures.

Public, so it says nothing but counts: never a company, a party, an amount or a document. Every
query filters by the pilot companies' ids (ADR-0003). The answer is kept for ten minutes, so a
page view, or a bot's thousand, costs no scan of the tables.
"""

import time
from typing import Final

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.platform.db import SessionDep

router = APIRouter(tags=["traction"])

_KEPT_FOR: Final = 600.0

# Each figure and its count over the pilot companies' rows.
_COUNTS: Final = {
    "messages": "SELECT count(*) FROM messages WHERE direction = 'inbound' "
    "AND company_id = ANY(:companies)",
    "files": "SELECT count(*) FROM documents WHERE company_id = ANY(:companies)",
    "invoices": "SELECT count(*) FROM invoices WHERE company_id = ANY(:companies)",
    "projects": "SELECT count(*) FROM projects WHERE company_id = ANY(:companies)",
    "bills_evaluated": "SELECT count(*) FROM decisions WHERE company_id = ANY(:companies)",
    "bills_held": "SELECT count(*) FROM decisions WHERE company_id = ANY(:companies) "
    "AND state = 'held'",
    "mirrored": "SELECT count(*) FROM payments WHERE company_id = ANY(:companies) "
    "AND kind = 'mirror' AND state = 'settled'",
}


class Traction(BaseModel):
    """Counts over every company using Tamga for real (kind `pilot`), all time."""

    companies: int
    # Mail, uploads and links received.
    messages: int
    # Files read: attachments and uploads.
    files: int
    # Invoices synced from the books.
    invoices: int
    # Jobs, proposed by Tamga or made by the owner.
    projects: int
    # Subcontractor bills weighed against the owner's rules, and those held now.
    bills_evaluated: int
    bills_held: int
    # Payments made by bank, each mirrored in test USDC on Arc.
    mirrored: int


# The last answer and when it was counted.
_kept: dict[str, tuple[float, Traction]] = {}


@router.get("/traction")
async def traction(db: SessionDep) -> Traction:
    now = time.monotonic()
    if (last := _kept.get("answer")) is not None and now - last[0] < _KEPT_FOR:
        return last[1]
    companies = list(
        (await db.execute(text("SELECT id FROM companies WHERE kind = 'pilot'"))).scalars()
    )
    counts = {
        name: int((await db.execute(text(query), {"companies": companies})).scalar_one())
        for name, query in _COUNTS.items()
    }
    answer = Traction(companies=len(companies), **counts)
    _kept["answer"] = (now, answer)
    return answer
