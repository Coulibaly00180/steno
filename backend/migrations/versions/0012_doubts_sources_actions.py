"""Doubtful words, sources of the summary lines, actions and decisions.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-24
"""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transcript_segments") as batch:
        batch.add_column(sa.Column("doubts", sa.Text(), nullable=True))
    with op.batch_alter_table("summaries") as batch:
        batch.add_column(sa.Column("sources", sa.Text(), nullable=True))
    op.create_table(
        "action_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "video_id", sa.String(36),
            sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_action_items_video_id"), nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.String(400), nullable=False),
        sa.Column("owner", sa.String(80), nullable=True),
        sa.Column("due_text", sa.String(80), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("start_seconds", sa.Float(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_action_items_video_id", "action_items", ["video_id"])
    op.create_index("ix_action_items_status_due", "action_items", ["status", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_action_items_status_due", table_name="action_items")
    op.drop_index("ix_action_items_video_id", table_name="action_items")
    op.drop_table("action_items")
    with op.batch_alter_table("summaries") as batch:
        batch.drop_column("sources")
    with op.batch_alter_table("transcript_segments") as batch:
        batch.drop_column("doubts")
