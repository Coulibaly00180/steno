"""Learning glossary, media policy, settings changed from the interface.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as batch:
        batch.add_column(sa.Column("source_policy", sa.String(16), nullable=False, server_default="keep"))
    op.create_table(
        "term_corrections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "video_id", sa.String(36),
            sa.ForeignKey("videos.id", ondelete="SET NULL", name="fk_term_corrections_video_id"),
            nullable=True,
        ),
        sa.Column("misheard", sa.String(120), nullable=False),
        sa.Column("term", sa.String(60), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_term_corrections_term_lower", "term_corrections", [sa.text("lower(term)")])
    op.create_table(
        "glossary_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("term", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_glossary_dismissals_term_lower", "glossary_dismissals", [sa.text("lower(term)")], unique=True)
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("uq_glossary_dismissals_term_lower", table_name="glossary_dismissals")
    op.drop_table("glossary_dismissals")
    op.drop_index("ix_term_corrections_term_lower", table_name="term_corrections")
    op.drop_table("term_corrections")
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("source_policy")
