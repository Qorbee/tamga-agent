"""payments recorded per invoice by default

Revision ID: 68c70e39a1e2
Revises: c8751d3867ba
Create Date: 2026-10-14 23:56:02.998717

"""

from collections.abc import Sequence

from alembic import op

revision: str = "68c70e39a1e2"
down_revision: str | Sequence[str] | None = "c8751d3867ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only the default for a version written without the field; versions already in force,
    # and what they say, stay as they are.
    op.alter_column("policies", "payments_recorded", server_default="per_invoice")


def downgrade() -> None:
    op.alter_column("policies", "payments_recorded", server_default="on_account")
