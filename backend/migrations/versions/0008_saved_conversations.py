"""Saved conversations on several videos, and interrupted answers.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-24
"""
import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_chat_messages") as batch:
        batch.add_column(sa.Column("interrupted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "library_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("scope", sa.String(200), nullable=True),
        sa.Column("video_ids", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_library_conversations_updated_at", "library_conversations", ["updated_at"])
    op.create_table(
        "library_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id", sa.String(36),
            sa.ForeignKey("library_conversations.id", ondelete="CASCADE", name="fk_library_messages_conversation_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("interrupted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_library_messages_conversation_id", "library_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_library_messages_conversation_id", table_name="library_messages")
    op.drop_table("library_messages")
    op.drop_index("ix_library_conversations_updated_at", table_name="library_conversations")
    op.drop_table("library_conversations")
    with op.batch_alter_table("video_chat_messages") as batch:
        batch.drop_column("interrupted")
