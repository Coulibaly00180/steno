"""Access tokens for the browser extension and scripts (feuille de route n° 4, phase 1).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-03
"""
import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="import"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_access_tokens"),
    )
    op.create_index("uq_access_tokens_token_hash", "access_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_access_tokens_token_hash", table_name="access_tokens")
    op.drop_table("access_tokens")
