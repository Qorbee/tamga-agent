"""a firm's balance in the books, and the bill's day on a decision

Revision ID: edfc92b29f32
Revises: 71bb988fb436
Create Date: 2026-10-05 15:09:53.699247

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "edfc92b29f32"
down_revision: str | Sequence[str] | None = "71bb988fb436"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("issued_on", sa.Date(), nullable=True))
    op.add_column("parties", sa.Column("balance", sa.Numeric(precision=16, scale=2), nullable=True))
    op.add_column("parties", sa.Column("balance_currency", sa.String(length=3), nullable=True))
    op.add_column("parties", sa.Column("balance_on", sa.Date(), nullable=True))
    # Autogenerate misses CHECK constraints.
    op.create_check_constraint(
        "parties_balance_currency_check",
        "parties",
        "(balance IS NULL) = (balance_currency IS NULL)",
    )
    # The decisions made so far take their bill's day; a firm's balance comes with the next read
    # of its card.
    op.execute(
        """
        UPDATE decisions SET issued_on = invoices.issue_date
        FROM invoices
        WHERE invoices.company_id = decisions.company_id AND invoices.id = decisions.invoice_id
        """
    )


def downgrade() -> None:
    op.drop_constraint("parties_balance_currency_check", "parties", type_="check")
    op.drop_column("parties", "balance_on")
    op.drop_column("parties", "balance_currency")
    op.drop_column("parties", "balance")
    op.drop_column("decisions", "issued_on")
