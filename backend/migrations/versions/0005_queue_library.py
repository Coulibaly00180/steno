"""Phase 3: job durations (estimates), tags, full-text search of the library.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23

Cancelled jobs need no column: CANCELLED is a new value of the existing
`status` strings. The full-text search column and its index exist only on
PostgreSQL (see app.schema.MIGRATION_ONLY_OBJECTS).
"""
import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_durations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("media_seconds", sa.Float(), nullable=False),
        sa.Column("elapsed_seconds", sa.Float(), nullable=False),
        sa.Column("translated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_job_durations_kind_finished_at", "job_durations", ["kind", "finished_at"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_tags_name_lower", "tags", [sa.text("lower(name)")], unique=True)
    op.create_table(
        "video_tags",
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_tags_video_id"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE", name="fk_video_tags_tag_id"), primary_key=True),
    )
    op.create_index("ix_video_tags_tag_id", "video_tags", ["tag_id"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Durations of the jobs already completed: estimates work from the first day.
    op.execute(
        "INSERT INTO job_durations (kind, media_seconds, elapsed_seconds, translated, finished_at) "
        "SELECT j.kind, v.duration_seconds, EXTRACT(EPOCH FROM (j.finished_at - j.started_at)), "
        "v.target_language IS NOT NULL, j.finished_at "
        "FROM processing_jobs j JOIN videos v ON v.id = j.video_id "
        "WHERE j.status = 'COMPLETED' AND j.started_at IS NOT NULL AND j.finished_at IS NOT NULL "
        "AND j.finished_at > j.started_at AND v.duration_seconds > 0"
    )
    # Accent-insensitive search ("reunion" finds "réunion"). unaccent() is only
    # STABLE: the IMMUTABLE wrapper with an explicit dictionary is the
    # documented way to use it in a generated column.
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        "CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
        "AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$"
    )
    # 'simple': no stemming, the library mixes languages. The "[hh:mm:ss]"
    # prefixes of every transcript line are removed: indexed, they made any
    # number ("07", "2024") match every transcript.
    op.execute(
        "ALTER TABLE videos ADD COLUMN search_vector tsvector GENERATED ALWAYS AS ("
        "to_tsvector('simple'::regconfig, f_unaccent(regexp_replace("
        "coalesce(original_filename, '') || ' ' || coalesce(transcript_text, '') || ' ' || coalesce(translated_text, ''), "
        "'\\[\\d{1,2}:\\d{2}(:\\d{2})?\\]', ' ', 'g'"
        ")))) STORED"
    )
    op.execute("CREATE INDEX ix_videos_search_vector ON videos USING gin (search_vector)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_videos_search_vector")
        op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS search_vector")
        op.execute("DROP FUNCTION IF EXISTS f_unaccent(text)")
    op.drop_index("ix_video_tags_tag_id", table_name="video_tags")
    op.drop_table("video_tags")
    op.drop_index("uq_tags_name_lower", table_name="tags")
    op.drop_table("tags")
    op.drop_index("ix_job_durations_kind_finished_at", table_name="job_durations")
    op.drop_table("job_durations")
