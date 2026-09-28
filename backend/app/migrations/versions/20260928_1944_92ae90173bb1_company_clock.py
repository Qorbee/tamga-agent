"""Company clock: a sandbox's offset and a replay's cursor (ADR-0009).

Revision ID: 92ae90173bb1
Revises: 7b1a40a4e419
Create Date: 2026-09-28 19:44:24.718027

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "92ae90173bb1"
down_revision: str | Sequence[str] | None = "7b1a40a4e419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "clock_offset", sa.Interval(), server_default=sa.text("interval '0'"), nullable=False
        ),
    )
    op.add_column(
        "companies", sa.Column("replay_cursor", sa.DateTime(timezone=True), nullable=True)
    )
    # Autogenerate does not see CHECK constraints. Existing rows pass both: none is a replay, and
    # every offset starts at zero.
    op.create_check_constraint(
        "companies_replay_cursor_check",
        "companies",
        "(mode = 'replay') = (replay_cursor IS NOT NULL)",
    )
    op.create_check_constraint(
        "companies_clock_offset_check",
        "companies",
        "kind = 'sandbox' OR clock_offset = interval '0'",
    )


def downgrade() -> None:
    op.drop_constraint("companies_clock_offset_check", "companies", type_="check")
    op.drop_constraint("companies_replay_cursor_check", "companies", type_="check")
    op.drop_column("companies", "replay_cursor")
    op.drop_column("companies", "clock_offset")
