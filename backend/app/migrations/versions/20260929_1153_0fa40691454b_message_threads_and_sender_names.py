"""message threads and sender names

Revision ID: 0fa40691454b
Revises: 21bca7ad4e3b
Create Date: 2026-09-29 11:53:41.576528

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0fa40691454b"
down_revision: str | Sequence[str] | None = "21bca7ad4e3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A chain of replies that name only their parent is joined one link per pass, until none moves. The
# cap only ends a loop of In-Reply-To headers (or two parents sharing a Message-ID) that never settles.
_MAX_PARENT_PASSES = 1000


def upgrade() -> None:
    # Stored mail gets its thread the way conversations.service._thread_key gives a new one: the
    # first reference, else the parent, else its own Message-ID, else its content hash. The sender's
    # name is not in any column; scripts/reparse_mail.py reads it from the stored originals.
    op.add_column("messages", sa.Column("thread_key", sa.Text(), nullable=True))
    op.create_index(
        "ix_messages_company_id_rfc_message_id",
        "messages",
        ["company_id", "rfc_message_id"],
        unique=False,
    )
    op.execute(
        """
        UPDATE messages SET thread_key = COALESCE(
            "references"[1], in_reply_to, rfc_message_id,
            '<' || encode(content_hash, 'hex') || '>'
        )
        """
    )
    for _ in range(_MAX_PARENT_PASSES):
        moved = op.get_bind().execute(
            sa.text(
                """
                UPDATE messages AS child SET thread_key = parent.thread_key
                FROM messages AS parent
                WHERE child.company_id = parent.company_id
                  AND cardinality(child."references") = 0
                  AND child.in_reply_to = parent.rfc_message_id
                  AND child.thread_key <> parent.thread_key
                """
            )
        )
        if moved.rowcount == 0:
            break
    op.alter_column("messages", "thread_key", nullable=False)
    op.add_column("messages", sa.Column("sender_name", sa.String(length=200), nullable=True))
    op.create_index(
        "ix_messages_company_id_thread_key", "messages", ["company_id", "thread_key"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_messages_company_id_thread_key", table_name="messages")
    op.drop_index("ix_messages_company_id_rfc_message_id", table_name="messages")
    op.drop_column("messages", "sender_name")
    op.drop_column("messages", "thread_key")
