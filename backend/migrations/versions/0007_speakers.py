"""Phase 5: speakers of a video and of each transcript segment.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speakers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_speakers_video_id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=True),
        sa.UniqueConstraint("video_id", "position", name="uq_speakers_video_position"),
    )
    op.create_index("ix_speakers_video_id", "speakers", ["video_id"])
    with op.batch_alter_table("transcript_segments") as batch:
        batch.add_column(sa.Column("speaker_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_transcript_segments_speaker_id", "speakers", ["speaker_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_transcript_segments_speaker_id", ["speaker_id"])
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("diarize", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("num_speakers", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("diarization_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("diarization_error")
        batch.drop_column("num_speakers")
        batch.drop_column("diarize")
    with op.batch_alter_table("transcript_segments") as batch:
        batch.drop_index("ix_transcript_segments_speaker_id")
        batch.drop_constraint("fk_transcript_segments_speaker_id", type_="foreignkey")
        batch.drop_column("speaker_id")
    op.drop_index("ix_speakers_video_id", table_name="speakers")
    op.drop_table("speakers")
