"""a file carried by mail is linked as the mail is

Revision ID: 6cdc43b1c69c
Revises: 5e506d9fe301
Create Date: 2026-10-03 19:20:00.524631

"""

from collections.abc import Sequence

from alembic import op

revision: str = "6cdc43b1c69c"
down_revision: str | Sequence[str] | None = "5e506d9fe301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Autogenerate does not see CHECK constraints: a link may now be a file's that came in a mail
    # of the project (ADR-0021). Every existing row passes the wider check.
    op.drop_constraint("project_links_method_check", "project_links", type_="check")
    op.create_check_constraint(
        "project_links_method_check",
        "project_links",
        "method IN ('key', 'scored', 'agent', 'owner', 'mail')",
    )


def downgrade() -> None:
    # A file's mail link is derived from its mail's links, so it can be built again.
    op.execute("DELETE FROM project_links WHERE method = 'mail'")
    op.drop_constraint("project_links_method_check", "project_links", type_="check")
    op.create_check_constraint(
        "project_links_method_check",
        "project_links",
        "method IN ('key', 'scored', 'agent', 'owner')",
    )
