"""Documents and conversations: files by content, mail with its folders and cursors (ADR-0008).

Revision ID: 1a47e5d442ea
Revises: 92ae90173bb1
Create Date: 2026-09-28 20:14:44.886023

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1a47e5d442ea"
down_revision: str | Sequence[str] | None = "92ae90173bb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=120), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
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
            name=op.f("documents_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("documents_pkey")),
        sa.UniqueConstraint("company_id", "sha256", name=op.f("documents_company_id_sha256_key")),
    )
    op.create_index(op.f("ix_documents_company_id"), "documents", ["company_id"], unique=False)
    op.create_table(
        "mail_folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("folder", sa.String(length=500), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("last_uid", sa.BigInteger(), nullable=False),
        sa.Column(
            "polled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("mail_folders_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("mail_folders_pkey")),
        sa.UniqueConstraint(
            "company_id",
            "mailbox",
            "folder",
            name=op.f("mail_folders_company_id_mailbox_folder_key"),
        ),
    )
    op.create_index(
        op.f("ix_mail_folders_company_id"), "mail_folders", ["company_id"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "email",
                "upload",
                "share",
                "link",
                "simulated",
                "whatsapp",
                name="messages_channel_check",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.Enum(
                "inbound",
                "outbound",
                name="messages_direction_check",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("raw_key", sa.String(length=120), nullable=True),
        sa.Column("rfc_message_id", sa.Text(), nullable=True),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column("references", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=True),
        sa.Column("recipients", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("messages_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("messages_pkey")),
        sa.UniqueConstraint(
            "company_id", "content_hash", name=op.f("messages_company_id_content_hash_key")
        ),
    )
    op.create_index(op.f("ix_messages_company_id"), "messages", ["company_id"], unique=False)
    op.create_index(op.f("ix_messages_sent_at"), "messages", ["sent_at"], unique=False)
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("message_attachments_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("message_attachments_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("message_attachments_message_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("message_attachments_pkey")),
        sa.UniqueConstraint(
            "message_id", "document_id", name=op.f("message_attachments_message_id_document_id_key")
        ),
    )
    op.create_index(
        op.f("ix_message_attachments_company_id"),
        "message_attachments",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_attachments_document_id"),
        "message_attachments",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_attachments_message_id"),
        "message_attachments",
        ["message_id"],
        unique=False,
    )
    op.create_table(
        "message_locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("mailbox", sa.String(length=320), nullable=False),
        sa.Column("folder", sa.String(length=500), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("message_locations_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("message_locations_message_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("message_locations_pkey")),
        sa.UniqueConstraint(
            "company_id",
            "mailbox",
            "folder",
            "uidvalidity",
            "uid",
            name=op.f("message_locations_company_id_mailbox_folder_uidvalidity_uid_key"),
        ),
    )
    op.create_index(
        op.f("ix_message_locations_company_id"), "message_locations", ["company_id"], unique=False
    )
    op.create_index(
        op.f("ix_message_locations_message_id"), "message_locations", ["message_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_message_locations_message_id"), table_name="message_locations")
    op.drop_index(op.f("ix_message_locations_company_id"), table_name="message_locations")
    op.drop_table("message_locations")
    op.drop_index(op.f("ix_message_attachments_message_id"), table_name="message_attachments")
    op.drop_index(op.f("ix_message_attachments_document_id"), table_name="message_attachments")
    op.drop_index(op.f("ix_message_attachments_company_id"), table_name="message_attachments")
    op.drop_table("message_attachments")
    op.drop_index(op.f("ix_messages_sent_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_company_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_mail_folders_company_id"), table_name="mail_folders")
    op.drop_table("mail_folders")
    op.drop_index(op.f("ix_documents_company_id"), table_name="documents")
    op.drop_table("documents")
