"""groups of firms and payment rules of a company's own

Revision ID: 76b54e50d7bf
Revises: 7f5a681b92cc
Create Date: 2026-10-15 20:49:33.109104

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "76b54e50d7bf"
down_revision: str | Sequence[str] | None = "7f5a681b92cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "party_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("party_ids", sa.ARRAY(sa.Uuid()), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("party_groups_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("party_groups_pkey")),
    )
    # A name is the company's once, whatever its case.
    op.create_index(
        "party_groups_company_id_lower_name_key",
        "party_groups",
        ["company_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        op.f("ix_party_groups_company_id"), "party_groups", ["company_id"], unique=False
    )
    op.add_column(
        "policies",
        sa.Column(
            "custom_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("policies", "custom_rules")
    op.drop_index("party_groups_company_id_lower_name_key", table_name="party_groups")
    op.drop_index(op.f("ix_party_groups_company_id"), table_name="party_groups")
    op.drop_table("party_groups")
