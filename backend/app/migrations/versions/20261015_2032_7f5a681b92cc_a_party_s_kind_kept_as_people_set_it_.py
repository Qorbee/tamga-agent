"""a party's kind kept as people set it, and an invoice's own file

Revision ID: 7f5a681b92cc
Revises: 68c70e39a1e2
Create Date: 2026-10-15 20:32:25.789033

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f5a681b92cc"
down_revision: str | Sequence[str] | None = "68c70e39a1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.add_column(
        "parties",
        sa.Column("kind_chosen", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("parties", "kind_chosen")
    op.drop_column("invoices", "document_id")
