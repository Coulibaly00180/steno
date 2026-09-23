"""Phase 4: transcript passages and their embeddings (pgvector), index state.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # Shipped by the project's PostgreSQL image (postgres/Dockerfile).
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "passages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_passages_video_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
    )
    op.create_index("ix_passages_video_id", "passages", ["video_id"])
    op.create_table(
        "video_indexes",
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_indexes_video_id"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("transcript_hash", sa.String(64), nullable=False),
        sa.Column("passages", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("video_indexes")
    op.drop_index("ix_passages_video_id", table_name="passages")
    op.drop_table("passages")
    # The extension stays: dropping it is harmless to skip and may be shared.
