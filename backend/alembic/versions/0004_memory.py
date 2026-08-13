"""entities, canon facts, summaries, notes

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = [
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
]


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ref", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("first_seen_session", sa.Integer(), nullable=True),
        sa.Column("mentions", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("known_to_players", sa.Boolean(), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_entities_campaign_id_campaigns", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_entities"),
    )
    op.create_index("ix_entities_campaign_id", "entities", ["campaign_id"])
    op.create_index("ix_entities_campaign_ref", "entities", ["campaign_id", "ref"], unique=True)

    op.create_table(
        "canon_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_ref", sa.String(160), nullable=False),
        sa.Column("predicate", sa.String(120), nullable=False),
        sa.Column("object_text", sa.Text(), nullable=False),
        sa.Column("source_turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_number", sa.Integer(), nullable=True),
        sa.Column("secret", sa.Boolean(), nullable=False),
        sa.Column("retracted", sa.Boolean(), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_canon_facts_campaign_id_campaigns", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["turns.id"], name="fk_canon_facts_source_turn_id_turns", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_canon_facts"),
    )
    op.create_index("ix_canon_facts_campaign_id", "canon_facts", ["campaign_id"])
    op.create_index("ix_canon_facts_subject_ref", "canon_facts", ["subject_ref"])

    op.create_table(
        "summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("session_number", sa.Integer(), nullable=True),
        sa.Column("entity_refs", postgresql.JSONB(), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_summaries_campaign_id_campaigns", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_summaries"),
    )
    op.create_index("ix_summaries_campaign_id", "summaries", ["campaign_id"])

    op.create_table(
        "notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("session_number", sa.Integer(), nullable=True),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_notes_campaign_id_campaigns", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name="fk_notes_author_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notes"),
    )
    op.create_index("ix_notes_campaign_id", "notes", ["campaign_id"])
    op.create_index("ix_notes_author_id", "notes", ["author_id"])


def downgrade() -> None:
    op.drop_table("notes")
    op.drop_table("summaries")
    op.drop_table("canon_facts")
    op.drop_table("entities")
