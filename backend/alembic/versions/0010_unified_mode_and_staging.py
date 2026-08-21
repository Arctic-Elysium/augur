"""collapse play modes, stage codex writes, give sessions a destination

Revision ID: 0010
Revises: 0009

Three changes that belong together because they are one decision: the human
is the authority on canon, and the model proposes.

1. `play_mode` goes. It encoded who runs the game, which `campaign_members.role`
   already encodes - solo is one person holding owner+gm+player, a table is a
   gm and several players. Two representations of one fact is how they drift
   apart. Everything that branched on the mode now branches on the role, or
   does not branch at all.

2. Entities and facts gain a status. Extraction proposes; the GM accepts.
   Nothing the model writes reaches durable canon unsupervised. Existing rows
   are grandfathered as accepted - they were written under the old rules and
   re-litigating a campaign's whole history at upgrade time helps nobody.

3. Sessions gain a destination and a pressure dial: where this sitting should
   end up, and how hard the world pushes toward it.

Note on `status` versus `known_to_players`: these are orthogonal and both are
kept. Status is whether the GM has accepted the entry into canon. Known is
whether the party has encountered it. A GM secret can be fully accepted canon,
and a staged entry can be perfectly public.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------- staging
    op.add_column(
        "entities",
        sa.Column("status", sa.String(16), nullable=False, server_default="accepted"),
    )
    # What the entry used to say, so a transformation keeps its history:
    # Borveld ran an inn, then died, then came back wrong. All three are true
    # in sequence, and the townspeople remember the inn.
    op.add_column(
        "entities",
        sa.Column(
            "history",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "entities",
        sa.Column("proposed_in_session", sa.Integer, nullable=True),
    )
    op.create_index(
        "ix_entities_campaign_status", "entities", ["campaign_id", "status"]
    )

    op.add_column(
        "canon_facts",
        sa.Column("status", sa.String(16), nullable=False, server_default="accepted"),
    )
    # Supersession, distinct from retraction. Retracted means it was never
    # true. Superseded means it was true and then stopped being - which the
    # model should still know, because the world remembers.
    op.add_column(
        "canon_facts",
        sa.Column("superseded_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "canon_facts",
        sa.Column("superseded_at_session", sa.Integer, nullable=True),
    )
    op.create_foreign_key(
        "fk_canon_facts_superseded_by",
        "canon_facts", "canon_facts",
        ["superseded_by_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_canon_facts_campaign_status", "canon_facts", ["campaign_id", "status"]
    )

    # ------------------------------------------------------------ destination
    op.add_column("play_sessions", sa.Column("destination", sa.Text, nullable=True))
    op.add_column(
        "play_sessions",
        sa.Column("pressure", sa.String(16), nullable=False, server_default="light"),
    )
    # A destination bound to a clock gives the model a mechanical sense of how
    # much session is left, which is the judgement it most obviously lacks.
    op.add_column(
        "play_sessions",
        sa.Column("destination_clock_id", sa.String(120), nullable=True),
    )
    op.add_column(
        "play_sessions",
        sa.Column("destination_reached", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    # -------------------------------------------------- prompt debug capture
    # Populated only when the campaign has debug_prompts on. Nullable and
    # unindexed: this is a development convenience, not a feature of play.
    op.add_column(
        "turns",
        sa.Column("prompt_debug", sa.dialects.postgresql.JSONB, nullable=True),
    )

    # --------------------------------------------------------- mode collapse
    # Anyone who was a member of a solo campaign owns it and therefore runs it,
    # so no role backfill is needed - the owner role already grants GM rights
    # through Access.runs_the_game.
    op.drop_column("campaigns", "play_mode")


def downgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("play_mode", sa.String(32), nullable=False, server_default="solo"),
    )
    op.drop_column("turns", "prompt_debug")
    op.drop_column("play_sessions", "destination_reached")
    op.drop_column("play_sessions", "destination_clock_id")
    op.drop_column("play_sessions", "pressure")
    op.drop_column("play_sessions", "destination")
    op.drop_index("ix_canon_facts_campaign_status", "canon_facts")
    op.drop_constraint("fk_canon_facts_superseded_by", "canon_facts")
    op.drop_column("canon_facts", "superseded_at_session")
    op.drop_column("canon_facts", "superseded_by_id")
    op.drop_column("canon_facts", "status")
    op.drop_index("ix_entities_campaign_status", "entities")
    op.drop_column("entities", "proposed_in_session")
    op.drop_column("entities", "history")
    op.drop_column("entities", "status")
