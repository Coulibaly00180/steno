"""Entities of the library, saved searches, feedback on answers.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-24
"""
import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_chat_messages") as batch:
        batch.add_column(sa.Column("feedback", sa.Integer(), nullable=True))
    with op.batch_alter_table("library_messages") as batch:
        batch.add_column(sa.Column("feedback", sa.Integer(), nullable=True))
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "merged_into", sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="SET NULL", name="fk_entities_merged_into"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "key", name="uq_entities_kind_key"),
    )
    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("entities.id", ondelete="CASCADE", name="fk_entity_mentions_entity_id"), nullable=False,
        ),
        sa.Column(
            "video_id", sa.String(36),
            sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_entity_mentions_video_id"), nullable=False,
        ),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
    )
    op.create_index("ix_entity_mentions_entity_id", "entity_mentions", ["entity_id"])
    op.create_index("ix_entity_mentions_video_id", "entity_mentions", ["video_id"])
    op.create_table(
        "video_entity_states",
        sa.Column(
            "video_id", sa.String(36),
            sa.ForeignKey("videos.id", ondelete="CASCADE", name="fk_video_entity_states_video_id"), primary_key=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("transcript_hash", sa.String(64), nullable=False),
        sa.Column("mentions", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_saved_searches_name_lower", "saved_searches", [sa.text("lower(name)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_saved_searches_name_lower", table_name="saved_searches")
    op.drop_table("saved_searches")
    op.drop_table("video_entity_states")
    op.drop_index("ix_entity_mentions_video_id", table_name="entity_mentions")
    op.drop_index("ix_entity_mentions_entity_id", table_name="entity_mentions")
    op.drop_table("entity_mentions")
    op.drop_table("entities")
    with op.batch_alter_table("library_messages") as batch:
        batch.drop_column("feedback")
    with op.batch_alter_table("video_chat_messages") as batch:
        batch.drop_column("feedback")
