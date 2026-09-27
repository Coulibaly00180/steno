"""Two sides of a recorded call: your microphone and the other side's sound.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27
"""
import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recordings") as batch:
        batch.add_column(sa.Column("sides", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("audio_layout", sa.String(length=16), nullable=True))
    with op.batch_alter_table("transcript_segments") as batch:
        batch.add_column(sa.Column("side", sa.String(length=8), nullable=True))
    with op.batch_alter_table("speakers") as batch:
        batch.add_column(sa.Column("side", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("side_position", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("speakers") as batch:
        batch.drop_column("side_position")
        batch.drop_column("side")
    with op.batch_alter_table("transcript_segments") as batch:
        batch.drop_column("side")
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("audio_layout")
    with op.batch_alter_table("recordings") as batch:
        batch.drop_column("sides")
