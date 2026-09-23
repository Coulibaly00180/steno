"""Analysis options: summary length, source language, vocabulary, global glossary.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("summary_length", sa.String(16), nullable=False, server_default="standard"),
    )
    op.add_column("summaries", sa.Column("summary_length", sa.String(16), nullable=True))
    op.add_column(
        "videos",
        sa.Column("source_language_forced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("videos", sa.Column("vocabulary", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column("glossary_snapshot", sa.Text(), nullable=True))

    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("term", sa.String(60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_glossary_terms_term_lower",
        "glossary_terms",
        [sa.text("lower(term)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_glossary_terms_term_lower", table_name="glossary_terms")
    op.drop_table("glossary_terms")
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("glossary_snapshot")
        batch.drop_column("vocabulary")
        batch.drop_column("source_language_forced")
    with op.batch_alter_table("summaries") as batch:
        batch.drop_column("summary_length")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("summary_length")
