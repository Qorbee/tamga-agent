"""The company boundary on business tables (ADR-0003).

Every business table mixes in ``CompanyScoped``; a test walks the models and fails on a table that
neither has the column nor is named in ``GLOBAL_TABLES``. The foreign key cascades so that deleting
an expired sandbox company deletes everything it owns.
"""

import uuid
from typing import Final

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

# The only tables without a company of their own. Keep this list short: every name here is a table
# whose queries the company filter cannot protect.
GLOBAL_TABLES: Final[frozenset[str]] = frozenset(
    # The anchors: one root over every company's log head (ADR-0003, ADR-0015). The reference
    # rates: public figures, the same for every company (ADR-0028).
    {"companies", "users", "memberships", "sessions", "decision_anchors", "fx_rates"}
)


class CompanyScoped:
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
