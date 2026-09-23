"""Chapters, summary regeneration jobs, transcript and summary editing.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("processing_jobs", sa.Column("kind", sa.String(16), nullable=False, server_default="FULL"))
    op.add_column("videos", sa.Column("transcript_edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("translated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("videos", sa.Column("summary_cache", sa.Text(), nullable=True))
    op.add_column("summaries", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
    )
    op.create_index("ix_chapters_video_id", "chapters", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_chapters_video_id", table_name="chapters")
    op.drop_table("chapters")
    with op.batch_alter_table("summaries") as batch:
        batch.drop_column("edited_at")
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("summary_cache")
        batch.drop_column("translated_at")
        batch.drop_column("transcript_edited_at")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("kind")
