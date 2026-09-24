"""Browser recordings with a live transcript, imports from a link.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("source_url", sa.Text(), nullable=True))
    op.create_table(
        "recordings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mime_type", sa.String(80), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column(
            "video_id", sa.String(36),
            sa.ForeignKey("videos.id", ondelete="SET NULL", name="fk_recordings_video_id"),
            nullable=True,
        ),
        sa.Column("live_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recordings_status", "recordings", ["status"])
    op.create_table(
        "live_segments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "recording_id", sa.String(36),
            sa.ForeignKey("recordings.id", ondelete="CASCADE", name="fk_live_segments_recording_id"),
            nullable=False,
        ),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_live_segments_recording_id", "live_segments", ["recording_id"])


def downgrade() -> None:
    op.drop_index("ix_live_segments_recording_id", table_name="live_segments")
    op.drop_table("live_segments")
    op.drop_index("ix_recordings_status", table_name="recordings")
    op.drop_table("recordings")
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("source_url")
