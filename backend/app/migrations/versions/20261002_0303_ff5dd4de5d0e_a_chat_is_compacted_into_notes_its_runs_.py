"""a chat is compacted into notes its runs are sent

Revision ID: ff5dd4de5d0e
Revises: b713bcd43e67
Create Date: 2026-10-02 03:03:35.910680

"""

from collections.abc import Sequence

from alembic import op

revision: str = "ff5dd4de5d0e"
down_revision: str | Sequence[str] | None = "b713bcd43e67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Autogenerate does not see CHECK constraints: a chat's row may now be the notes of a
    # compaction. Every existing row passes the wider check.
    op.drop_constraint("chat_messages_kind_check", "chat_messages", type_="check")
    op.create_check_constraint(
        "chat_messages_kind_check", "chat_messages", "kind IN ('model', 'card', 'notes')"
    )


def downgrade() -> None:
    # Notes stand in for messages that are all still stored: without them a chat is sent whole.
    op.execute("DELETE FROM chat_messages WHERE kind = 'notes'")
    op.drop_constraint("chat_messages_kind_check", "chat_messages", type_="check")
    op.create_check_constraint(
        "chat_messages_kind_check", "chat_messages", "kind IN ('model', 'card')"
    )
