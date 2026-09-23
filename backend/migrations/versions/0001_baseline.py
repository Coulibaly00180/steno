"""Baseline: the schema created by Base.metadata.create_all before Alembic.

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def baseline_metadata() -> sa.MetaData:
    """Frozen copy of the pre-Alembic schema.

    app.migrate compares legacy databases against this metadata, so it must
    never follow later model changes: those belong to newer revisions.
    """
    metadata = sa.MetaData()
    sa.Table(
        "videos", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detected_language", sa.String(32), nullable=True),
        sa.Column("target_language", sa.String(32), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("translated_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "processing_jobs", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("template_id", sa.String(36), nullable=True),
        sa.Column("custom_prompt", sa.Text(), nullable=True),
        sa.Column("rq_job_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    sa.Table(
        "transcript_segments", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    sa.Table(
        "summary_templates", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "summaries", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("template_id", sa.String(36), nullable=True),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "video_chat_messages", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    return metadata


def upgrade() -> None:
    baseline_metadata().create_all(bind=op.get_bind())


def downgrade() -> None:
    raise RuntimeError(
        "La révision de référence ne se défait pas : restaurez une sauvegarde (make backup) "
        "ou réinitialisez la base (make reset)."
    )
