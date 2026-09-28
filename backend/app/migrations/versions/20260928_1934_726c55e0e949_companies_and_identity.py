"""Companies and identity: tenants, users, memberships, sessions, invites (ADR-0003, ADR-0004).

Revision ID: 726c55e0e949
Revises:
Create Date: 2026-09-28 19:34:02.237392

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "726c55e0e949"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "pilot",
                "demo",
                "sandbox",
                name="companies_kind_check",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum(
                "replay",
                "shadow",
                "live",
                name="companies_mode_check",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("companies_pkey")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("password_hash", sa.String(length=200), nullable=True),
        sa.Column("is_guest", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "is_guest OR (email IS NOT NULL AND password_hash IS NOT NULL)",
            name="users_email_and_password_check",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("users_pkey")),
        sa.UniqueConstraint("email", name=op.f("users_email_key")),
    )
    op.create_table(
        "invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "ops",
                "viewer",
                name="invites_role_check",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("invites_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("invites_created_by_fkey"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("invites_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("invites_token_hash_key")),
    )
    op.create_index(op.f("ix_invites_company_id"), "invites", ["company_id"], unique=False)
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "ops",
                "viewer",
                name="memberships_role_check",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("memberships_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("memberships_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("memberships_pkey")),
        sa.UniqueConstraint(
            "user_id", "company_id", name=op.f("memberships_user_id_company_id_key")
        ),
    )
    op.create_index(op.f("ix_memberships_company_id"), "memberships", ["company_id"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("sessions_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("sessions_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("sessions_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("sessions_token_hash_key")),
    )
    op.create_index(op.f("ix_sessions_company_id"), "sessions", ["company_id"], unique=False)
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_company_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_memberships_company_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_invites_company_id"), table_name="invites")
    op.drop_table("invites")
    op.drop_table("users")
    op.drop_table("companies")
