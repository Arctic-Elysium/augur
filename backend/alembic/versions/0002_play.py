"""characters, play sessions, turns, clocks, check locks

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = [
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
]


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ruleset_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("controller", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sheet", postgresql.JSONB(), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_characters_campaign_id_campaigns", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_characters_owner_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_characters"),
    )
    op.create_index("ix_characters_campaign_id", "characters", ["campaign_id"])
    op.create_index("ix_characters_owner_id", "characters", ["owner_id"])

    op.create_table(
        "play_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scene_id", sa.String(120), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("active_character_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_play_sessions_campaign_id_campaigns", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["active_character_id"], ["characters.id"], name="fk_play_sessions_active_character_id_characters", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_play_sessions"),
        sa.UniqueConstraint("campaign_id", "number", name="uq_play_sessions_campaign_id"),
    )
    op.create_index("ix_play_sessions_campaign_id", "play_sessions", ["campaign_id"])

    op.create_table(
        "turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("player_input", sa.Text(), nullable=False),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False),
        sa.Column("deltas", postgresql.JSONB(), nullable=False),
        sa.Column("scene_id", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["session_id"], ["play_sessions.id"], name="fk_turns_session_id_play_sessions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["characters.id"], name="fk_turns_actor_id_characters", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_turns_session_id"),
    )
    op.create_index("ix_turns_session_id", "turns", ["session_id"])

    op.create_table(
        "session_clocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clock_id", sa.String(120), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("filled", sa.Integer(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_session_clocks_campaign_id_campaigns", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_session_clocks"),
        sa.UniqueConstraint("campaign_id", "clock_id", name="uq_session_clocks_campaign_id"),
    )
    op.create_index("ix_session_clocks_campaign_id", "session_clocks", ["campaign_id"])

    op.create_table(
        "check_locks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_ref", sa.String(64), nullable=False),
        sa.Column("kind_id", sa.String(64), nullable=False),
        sa.Column("target_ref", sa.String(300), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("scene_id", sa.String(120), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        *_TS,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_check_locks_campaign_id_campaigns", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_check_locks"),
        sa.UniqueConstraint("campaign_id", "actor_ref", "kind_id", "target_ref", "fingerprint", name="uq_check_locks_campaign_id"),
    )
    op.create_index("ix_check_locks_campaign_id", "check_locks", ["campaign_id"])


def downgrade() -> None:
    op.drop_table("check_locks")
    op.drop_table("session_clocks")
    op.drop_table("turns")
    op.drop_table("play_sessions")
    op.drop_table("characters")
