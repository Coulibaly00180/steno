"""Time spent in each stage of a job, kept with its duration.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25
"""
import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(sa.Column("stage_times", sa.Text(), nullable=True))
    with op.batch_alter_table("job_durations") as batch:
        batch.add_column(sa.Column("stages", sa.Text(), nullable=True))
        batch.add_column(sa.Column("queued_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_durations") as batch:
        batch.drop_column("queued_seconds")
        batch.drop_column("stages")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("stage_times")
