"""a decision whose books show the bill paid

Revision ID: a3249a9f6839
Revises: 3aaedd4cfad1
Create Date: 2026-10-08 15:37:50.743471

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3249a9f6839"
down_revision: str | Sequence[str] | None = "3aaedd4cfad1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES_BEFORE = "'awaiting_owner', 'held', 'rejected', 'approved', 'settled', 'failed'"
_STATES_AFTER = _STATES_BEFORE + ", 'paid_in_books'"


def upgrade() -> None:
    # Autogenerate does not see CHECK constraints. Every existing row passes the wider check.
    op.drop_constraint("decisions_state_check", "decisions", type_="check")
    op.create_check_constraint("decisions_state_check", "decisions", f"state IN ({_STATES_AFTER})")


def downgrade() -> None:
    # A rule holds each of them: held is what they were before the books' reading was told apart.
    op.execute("UPDATE decisions SET state = 'held' WHERE state = 'paid_in_books'")
    op.drop_constraint("decisions_state_check", "decisions", type_="check")
    op.create_check_constraint("decisions_state_check", "decisions", f"state IN ({_STATES_BEFORE})")
