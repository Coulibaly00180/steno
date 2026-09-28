"""The side of each live transcript line in a two-sided recording.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-28
"""
import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("live_segments") as batch:
        batch.add_column(sa.Column("side", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("live_segments") as batch:
        batch.drop_column("side")
