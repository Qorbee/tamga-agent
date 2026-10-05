"""an owner's cleared type keeps the owner's mark

Revision ID: 7b5b1282077b
Revises: edfc92b29f32
Create Date: 2026-10-05 16:17:45.035341

"""

from collections.abc import Sequence

from alembic import op

revision: str = "7b5b1282077b"
down_revision: str | Sequence[str] | None = "edfc92b29f32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A check constraint is not compared by autogenerate: written by hand.
    op.drop_constraint("documents_doc_type_source_set", "documents", type_="check")
    op.create_check_constraint(
        "documents_doc_type_source_set",
        "documents",
        "doc_type IS NOT NULL AND doc_type_source IS NOT NULL "
        "OR doc_type IS NULL AND (doc_type_source IS NULL OR doc_type_source = 'owner')",
    )


def downgrade() -> None:
    op.execute("UPDATE documents SET doc_type_source = NULL WHERE doc_type IS NULL")
    op.drop_constraint("documents_doc_type_source_set", "documents", type_="check")
    op.create_check_constraint(
        "documents_doc_type_source_set",
        "documents",
        "(doc_type IS NULL) = (doc_type_source IS NULL)",
    )
