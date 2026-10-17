"""a shadow mirror is logged as mirrored

Revision ID: f75a80c9a5db
Revises: 8e4008d6e985
Create Date: 2026-10-17 23:16:32.451304

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f75a80c9a5db"
down_revision: str | Sequence[str] | None = "8e4008d6e985"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENTS_BEFORE = (
    "'proposed', 'edited', 'approved', 'held', 'rejected', 'settled', 'failed', "
    "'would_release', 'advised', 'pay_by', 'unpaid'"
)
_EVENTS_AFTER = _EVENTS_BEFORE + ", 'mirrored'"


def upgrade() -> None:
    op.drop_constraint("decision_log_event_check", "decision_log", type_="check")
    op.create_check_constraint(
        "decision_log_event_check", "decision_log", f"event IN ({_EVENTS_AFTER})"
    )


def downgrade() -> None:
    # A mirrored event is a link in the company's hash chain: it cannot be dropped to go back.
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT count(*) FROM decision_log WHERE event = 'mirrored'").scalar():
        raise RuntimeError("Mirrored events exist: restore a backup taken before this revision")
    op.drop_constraint("decision_log_event_check", "decision_log", type_="check")
    op.create_check_constraint(
        "decision_log_event_check", "decision_log", f"event IN ({_EVENTS_BEFORE})"
    )
