"""Meeting series, video clips, quality runs on the reference corpus.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-24
"""
import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_series",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_meeting_series_name_lower", "meeting_series", [sa.text("lower(name)")], unique=True)
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("series_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("series_changes", sa.Text(), nullable=True))
        batch.create_foreign_key("fk_videos_series_id", "meeting_series", ["series_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_videos_series_id", ["series_id"])

    op.create_table(
        "video_clips",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "video_id", sa.String(36),
            sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_clips_video_id"), nullable=False,
        ),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("subtitles", sa.String(16), nullable=False, server_default="none"),
        sa.Column("subtitle_source", sa.String(16), nullable=False, server_default="original"),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_video_clips_video_id", "video_clips", ["video_id"])

    op.create_table(
        "quality_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("llm_model", sa.String(120), nullable=False),
        sa.Column("whisper_model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current", sa.String(200), nullable=True),
        sa.Column("results", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("rq_job_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_quality_runs_created_at", "quality_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_quality_runs_created_at", table_name="quality_runs")
    op.drop_table("quality_runs")
    op.drop_index("ix_video_clips_video_id", table_name="video_clips")
    op.drop_table("video_clips")
    with op.batch_alter_table("videos") as batch:
        batch.drop_index("ix_videos_series_id")
        batch.drop_constraint("fk_videos_series_id", type_="foreignkey")
        batch.drop_column("series_changes")
        batch.drop_column("series_id")
    op.drop_index("uq_meeting_series_name_lower", table_name="meeting_series")
    op.drop_table("meeting_series")
